#  Copyright 2021 Collate
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#  http://www.apache.org/licenses/LICENSE-2.0
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
"""
Dagster source to extract metadata from OM UI
"""
import traceback
from typing import Iterable, List, Optional

from metadata.generated.schema.api.data.createPipeline import CreatePipelineRequest
from metadata.generated.schema.api.lineage.addLineage import AddLineageRequest
from metadata.generated.schema.entity.data.pipeline import (
    PipelineStatus,
    StatusType,
    Task,
    TaskStatus,
)
from metadata.generated.schema.entity.services.connections.metadata.openMetadataConnection import (
    OpenMetadataConnection,
)
from metadata.generated.schema.entity.services.connections.pipeline.dagsterConnection import (
    DagsterConnection,
)
from metadata.generated.schema.metadataIngestion.workflow import (
    Source as WorkflowSource,
)
from metadata.ingestion.api.source import InvalidSourceException
from metadata.ingestion.models.ometa_classification import OMetaTagAndClassification
from metadata.ingestion.models.pipeline_status import OMetaPipelineStatus
from metadata.ingestion.source.pipeline.dagster.models import (
    DagsterPipeline,
    RunStepStats,
    SolidHandle,
)
from metadata.ingestion.source.pipeline.pipeline_service import PipelineServiceSource
from metadata.utils.helpers import clean_uri
from metadata.utils.logger import ingestion_logger
from metadata.utils.tag_utils import get_ometa_tag_and_classification, get_tag_labels

logger = ingestion_logger()

STATUS_MAP = {
    "success": StatusType.Successful.value,
    "failure": StatusType.Failed.value,
    "queued": StatusType.Pending.value,
}

DAGSTER_TAG_CATEGORY = "DagsterTags"


class DagsterSource(PipelineServiceSource):
    """
    Implements the necessary methods ot extract
    Pipeline metadata from Dagster's metadata db
    """

    @classmethod
    def create(cls, config_dict, metadata_config: OpenMetadataConnection):
        config: WorkflowSource = WorkflowSource.parse_obj(config_dict)
        connection: DagsterConnection = config.serviceConnection.__root__.config
        if not isinstance(connection, DagsterConnection):
            raise InvalidSourceException(
                f"Expected DagsterConnection, but got {connection}"
            )
        return cls(config, metadata_config)

    def _get_downstream_tasks(self, job: SolidHandle) -> Optional[List[str]]:
        """
        Method to get downstream tasks
        """
        down_stream_tasks = []
        if job.solid:
            for tasks in job.solid.inputs or []:
                if tasks:
                    for task in tasks.dependsOn or []:
                        down_stream_tasks.append(task.solid.name)
        return down_stream_tasks or None

    def _get_task_list(self, pipeline_name: str) -> Optional[List[Task]]:
        """
        Method to collect all the tasks from dagster and return it in a task list
        """
        jobs = self.client.get_jobs(
            pipeline_name=pipeline_name,
            repository_name=self.context.repository_name,
            repository_location=self.context.repository_location,
        )
        task_list: List[Task] = []
        if jobs:
            for job in jobs.solidHandles or []:
                try:
                    task = Task(
                        name=job.handleID,
                        displayName=job.handleID,
                        downstreamTasks=self._get_downstream_tasks(job=job),
                        sourceUrl=self.get_source_url(
                            pipeline_name=pipeline_name, task_name=job.handleID
                        ),
                    )
                    task_list.append(task)
                except Exception as exc:
                    logger.debug(traceback.format_exc())
                    logger.warning(
                        f"Error to fetch tasks for {pipeline_name}:{job}: {exc}"
                    )

        return task_list or None

    def yield_pipeline(
        self, pipeline_details: DagsterPipeline
    ) -> Iterable[CreatePipelineRequest]:
        """
        Convert a DAG into a Pipeline Entity
        :param serialized_dag: SerializedDAG from dagster metadata DB
        :return: Create Pipeline request with tasks
        """

        try:
            pipeline_request = CreatePipelineRequest(
                name=pipeline_details.id.replace(":", ""),
                displayName=pipeline_details.name,
                description=pipeline_details.description,
                tasks=self._get_task_list(pipeline_name=pipeline_details.name),
                service=self.context.pipeline_service.fullyQualifiedName.__root__,
                tags=get_tag_labels(
                    metadata=self.metadata,
                    tags=[self.context.repository_name],
                    classification_name=DAGSTER_TAG_CATEGORY,
                    include_tags=self.source_config.includeTags,
                ),
                sourceUrl=self.get_source_url(
                    pipeline_name=pipeline_details.name, task_name=None
                ),
            )
            yield pipeline_request
            self.register_record(pipeline_request=pipeline_request)
        except Exception as exc:
            logger.debug(traceback.format_exc())
            logger.warning(f"Error to yield pipeline for {pipeline_details}: {exc}")

    def yield_tag(self, *_, **__) -> Iterable[OMetaTagAndClassification]:
        yield from get_ometa_tag_and_classification(
            tags=[self.context.repository_name],
            classification_name=DAGSTER_TAG_CATEGORY,
            tag_description="Dagster Tag",
            classification_desciption="Tags associated with dagster entities",
            include_tags=self.source_config.includeTags,
        )

    def _get_task_status(
        self, run: RunStepStats, task_name: str
    ) -> Iterable[OMetaPipelineStatus]:
        """
        Prepare the OMetaPipelineStatus
        """
        try:
            task_status = TaskStatus(
                name=task_name,
                executionStatus=STATUS_MAP.get(
                    run.status.lower(), StatusType.Pending.value
                ),
                startTime=round(run.startTime) if run.startTime else None,
                endTime=round(run.endTime) if run.endTime else None,
            )

            pipeline_status = PipelineStatus(
                taskStatus=[task_status],
                executionStatus=STATUS_MAP.get(
                    run.status.lower(), StatusType.Pending.value
                ),
                timestamp=round(run.endTime) if run.endTime else None,
            )
            pipeline_status_yield = OMetaPipelineStatus(
                pipeline_fqn=self.context.pipeline.fullyQualifiedName.__root__,
                pipeline_status=pipeline_status,
            )
            yield pipeline_status_yield
        except Exception as exc:
            logger.debug(traceback.format_exc())
            logger.warning(f"Error to yield run status for {run}: {exc}")

    def yield_pipeline_status(
        self, pipeline_details: DagsterPipeline
    ) -> Iterable[OMetaPipelineStatus]:
        """
        Yield the pipeline and task status
        """
        for task in self.context.pipeline.tasks or []:
            try:
                runs = self.client.get_task_runs(
                    task.name,
                    pipeline_name=pipeline_details.name,
                    repository_name=self.context.repository_name,
                    repository_location=self.context.repository_location,
                )
                for run in runs.solidHandle.stepStats.nodes or []:
                    yield from self._get_task_status(run=run, task_name=task.name)
            except Exception as exc:
                logger.debug(traceback.format_exc())
                logger.warning(
                    f"Error to yield pipeline status for {pipeline_details}: {exc}"
                )

    def yield_pipeline_lineage_details(
        self, pipeline_details: DagsterPipeline
    ) -> Optional[Iterable[AddLineageRequest]]:
        """
        Not implemented, as this connector does not create any lineage
        """

    def get_pipelines_list(self) -> Iterable[DagsterPipeline]:
        """
        Get List of all pipelines
        """
        try:
            results = self.client.get_run_list()
            for result in results:
                self.context.repository_location = result.location.name
                self.context.repository_name = result.name
                for job in result.pipelines or []:
                    yield job
        except Exception as exc:
            logger.debug(traceback.format_exc())
            logger.error(
                f"Unable to get pipelines list\n"
                f"Please check if dagster is running correctly and is in good state: {exc}"
            )

    def get_pipeline_name(self, pipeline_details: DagsterPipeline) -> str:
        """
        Get Pipeline Name
        """

        return pipeline_details.name

    def get_source_url(
        self, pipeline_name: str, task_name: Optional[str]
    ) -> Optional[str]:
        """
        Method to get source url for pipelines and tasks for dagster
        """
        try:
            url = (
                f"{clean_uri(self.service_connection.host)}/locations/"
                f"{self.context.repository_location}/jobs/{pipeline_name}/"
            )
            if task_name:
                url = f"{url}{task_name}"
            return url
        except Exception as exc:
            logger.debug(traceback.format_exc())
            logger.warning(f"Error to get pipeline url: {exc}")
        return None

    def test_connection(self) -> None:
        pass
