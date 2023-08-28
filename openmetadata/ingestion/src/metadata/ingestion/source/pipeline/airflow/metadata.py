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
Airflow source to extract metadata from OM UI
"""
import traceback
from datetime import datetime
from typing import Iterable, List, Optional, cast

from airflow.models import BaseOperator, DagRun, TaskInstance
from airflow.models.serialized_dag import SerializedDagModel
from airflow.serialization.serialized_objects import SerializedDAG
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from metadata.generated.schema.api.data.createPipeline import CreatePipelineRequest
from metadata.generated.schema.api.lineage.addLineage import AddLineageRequest
from metadata.generated.schema.entity.data.pipeline import (
    PipelineStatus,
    StatusType,
    Task,
    TaskStatus,
)
from metadata.generated.schema.entity.data.table import Table
from metadata.generated.schema.entity.services.connections.metadata.openMetadataConnection import (
    OpenMetadataConnection,
)
from metadata.generated.schema.entity.services.connections.pipeline.airflowConnection import (
    AirflowConnection,
)
from metadata.generated.schema.metadataIngestion.workflow import (
    Source as WorkflowSource,
)
from metadata.generated.schema.type.entityLineage import EntitiesEdge, LineageDetails
from metadata.generated.schema.type.entityReference import EntityReference
from metadata.ingestion.api.source import InvalidSourceException
from metadata.ingestion.connections.session import create_and_bind_session
from metadata.ingestion.models.pipeline_status import OMetaPipelineStatus
from metadata.ingestion.source.pipeline.airflow.lineage_parser import get_xlets_from_dag
from metadata.ingestion.source.pipeline.airflow.models import (
    AirflowDag,
    AirflowDagDetails,
)
from metadata.ingestion.source.pipeline.airflow.utils import get_schedule_interval
from metadata.ingestion.source.pipeline.pipeline_service import PipelineServiceSource
from metadata.utils.helpers import clean_uri, datetime_to_ts
from metadata.utils.logger import ingestion_logger

logger = ingestion_logger()

STATUS_MAP = {
    "success": StatusType.Successful.value,
    "failed": StatusType.Failed.value,
    "queued": StatusType.Pending.value,
}


class OMTaskInstance(BaseModel):
    """
    Custom model we get from the Airflow db
    as a scoped SELECT from TaskInstance
    """

    task_id: str
    state: str
    start_date: Optional[datetime]
    end_date: Optional[datetime]


class AirflowSource(PipelineServiceSource):
    """
    Implements the necessary methods ot extract
    Pipeline metadata from Airflow's metadata db
    """

    def __init__(
        self,
        config: WorkflowSource,
        metadata_config: OpenMetadataConnection,
    ):
        super().__init__(config, metadata_config)
        self._session = None

    @classmethod
    def create(cls, config_dict, metadata_config: OpenMetadataConnection):
        config: WorkflowSource = WorkflowSource.parse_obj(config_dict)
        connection: AirflowConnection = config.serviceConnection.__root__.config
        if not isinstance(connection, AirflowConnection):
            raise InvalidSourceException(
                f"Expected AirflowConnection, but got {connection}"
            )
        return cls(config, metadata_config)

    @property
    def session(self) -> Session:
        """
        Return the SQLAlchemy session from the engine
        """
        if not self._session:
            self._session = create_and_bind_session(self.connection)

        return self._session

    def get_pipeline_status(self, dag_id: str) -> List[DagRun]:
        """
        Return the DagRuns of given dag
        """
        dag_run_list = (
            self.session.query(
                DagRun.dag_id,
                DagRun.run_id,
                DagRun.queued_at,
                DagRun.execution_date,
                DagRun.start_date,
                DagRun.state,
            )
            .filter(DagRun.dag_id == dag_id)
            .order_by(DagRun.execution_date.desc())
            .limit(self.config.serviceConnection.__root__.config.numberOfStatus)
            .all()
        )

        dag_run_dict = [dict(elem) for elem in dag_run_list]

        # Build DagRun manually to not fall into new/old columns from
        # different Airflow versions
        return [
            DagRun(
                dag_id=elem.get("dag_id"),
                run_id=elem.get("run_id"),
                queued_at=elem.get("queued_at"),
                execution_date=elem.get("execution_date"),
                start_date=elem.get("start_date"),
                state=elem.get("state"),
            )
            for elem in dag_run_dict
        ]

    def get_task_instances(self, dag_id: str, run_id: str) -> List[OMTaskInstance]:
        """
        We are building our own scoped TaskInstance
        class to only focus on core properties required
        by the metadata ingestion.

        This makes the versioning more flexible on which Airflow
        sources we support.
        """
        task_instance_list = None

        try:
            task_instance_list = (
                self.session.query(
                    TaskInstance.task_id,
                    TaskInstance.state,
                    TaskInstance.start_date,
                    TaskInstance.end_date,
                    TaskInstance.run_id,
                )
                .filter(TaskInstance.dag_id == dag_id, TaskInstance.run_id == run_id)
                .all()
            )
        except Exception as exc:  # pylint: disable=broad-except
            # Using a broad Exception here as the backend can come in many flavours (pymysql, pyodbc...)
            # And we don't want to force all imports
            logger.debug(traceback.format_exc())
            logger.warning(
                f"Tried to get TaskInstances with run_id. It might not be available in older Airflow versions - {exc}."
            )

        task_instance_dict = (
            [dict(elem) for elem in task_instance_list] if task_instance_list else []
        )

        return [
            OMTaskInstance(
                task_id=elem.get("task_id"),
                state=elem.get("state"),
                start_date=elem.get("start_date"),
                end_date=elem.get("end_date"),
            )
            for elem in task_instance_dict
        ]

    def yield_pipeline_status(
        self, pipeline_details: AirflowDagDetails
    ) -> OMetaPipelineStatus:
        try:
            dag_run_list = self.get_pipeline_status(pipeline_details.dag_id)

            for dag_run in dag_run_list:
                if (
                    dag_run.run_id
                ):  # Airflow dags can have old task which are turned off/commented out in code
                    tasks = self.get_task_instances(
                        dag_id=dag_run.dag_id, run_id=dag_run.run_id
                    )

                    task_statuses = [
                        TaskStatus(
                            name=task.task_id,
                            executionStatus=STATUS_MAP.get(
                                task.state, StatusType.Pending.value
                            ),
                            startTime=datetime_to_ts(task.start_date),
                            endTime=datetime_to_ts(
                                task.end_date
                            ),  # Might be None for running tasks
                        )  # Log link might not be present in all Airflow versions
                        for task in tasks
                    ]

                    pipeline_status = PipelineStatus(
                        taskStatus=task_statuses,
                        executionStatus=STATUS_MAP.get(
                            dag_run.state, StatusType.Pending.value
                        ),
                        timestamp=dag_run.execution_date.timestamp(),
                    )
                    yield OMetaPipelineStatus(
                        pipeline_fqn=self.context.pipeline.fullyQualifiedName.__root__,
                        pipeline_status=pipeline_status,
                    )
        except Exception as exc:
            logger.debug(traceback.format_exc())
            logger.warning(
                f"Wild error trying to extract status from DAG {pipeline_details.dag_id} - {exc}."
                " Skipping status ingestion."
            )

    def get_pipelines_list(self) -> Iterable[AirflowDagDetails]:
        """
        List all DAGs from the metadata db.

        We are using the SerializedDagModel as it helps
        us retrieve all the task and inlets/outlets information
        """

        json_data_column = (
            SerializedDagModel._data  # For 2.3.0 onwards # pylint: disable=protected-access
            if hasattr(SerializedDagModel, "_data")
            else SerializedDagModel.data  # For 2.2.5 and 2.1.4
        )
        for serialized_dag in self.session.query(
            SerializedDagModel.dag_id,
            json_data_column,
            SerializedDagModel.fileloc,
        ).all():
            try:
                data = serialized_dag[1]["dag"]
                dag = AirflowDagDetails(
                    dag_id=serialized_dag[0],
                    fileloc=serialized_dag[2],
                    data=AirflowDag.parse_obj(serialized_dag[1]),
                    max_active_runs=data.get("max_active_runs", None),
                    description=data.get("_description", None),
                    start_date=data.get("start_date", None),
                    tasks=data.get("tasks", []),
                    schedule_interval=get_schedule_interval(data),
                    owners=self.fetch_owners(data),
                )

                yield dag
            except ValidationError as err:
                logger.debug(traceback.format_exc())
                logger.warning(
                    f"Error building pydantic model for {serialized_dag} - {err}"
                )
            except Exception as err:
                logger.debug(traceback.format_exc())
                logger.warning(f"Wild error yielding dag {serialized_dag} - {err}")

    def fetch_owners(self, data) -> Optional[str]:
        try:
            if self.source_config.includeOwners and data.get("default_args"):
                return data.get("default_args", [])["__var"].get("email", [])
        except TypeError:
            pass
        return None

    def get_pipeline_name(self, pipeline_details: SerializedDAG) -> str:
        """
        Get Pipeline Name
        """
        return pipeline_details.dag_id

    @staticmethod
    def get_tasks_from_dag(dag: AirflowDagDetails, host_port: str) -> List[Task]:
        """
        Obtain the tasks from a SerializedDAG
        :param dag: AirflowDagDetails
        :param host_port: service host
        :return: List of tasks
        """
        return [
            Task(
                name=task.task_id,
                description=task.doc_md,
                sourceUrl=(
                    f"{clean_uri(host_port)}/taskinstance/list/"
                    f"?flt1_dag_id_equals={dag.dag_id}&_flt_3_task_id={task.task_id}"
                ),
                downstreamTasks=list(task.downstream_task_ids)
                if task.downstream_task_ids
                else [],
                startDate=task.start_date.isoformat() if task.start_date else None,
                endDate=task.end_date.isoformat() if task.end_date else None,
                taskType=task.task_type,
            )
            for task in cast(Iterable[BaseOperator], dag.tasks)
        ]

    def get_user_details(self, email) -> Optional[EntityReference]:
        user = self.metadata.get_user_by_email(email=email)
        if user:
            return EntityReference(id=user.id.__root__, type="user")
        return None

    def get_owner(self, owners) -> Optional[EntityReference]:
        try:
            if isinstance(owners, str) and owners:
                return self.get_user_details(email=owners)

            if isinstance(owners, List) and owners:
                for owner in owners or []:
                    return self.get_user_details(email=owner)

            logger.debug(f"No user found with email [{owners}] in OMD")
        except Exception as exc:
            logger.warning(f"Error while getting details of user {owners} - {exc}")
        return None

    def yield_pipeline(
        self, pipeline_details: AirflowDagDetails
    ) -> Iterable[CreatePipelineRequest]:
        """
        Convert a DAG into a Pipeline Entity
        :param pipeline_details: SerializedDAG from airflow metadata DB
        :return: Create Pipeline request with tasks
        """

        try:

            pipeline_request = CreatePipelineRequest(
                name=pipeline_details.dag_id,
                description=pipeline_details.description,
                sourceUrl=f"{clean_uri(self.service_connection.hostPort)}/tree?dag_id={pipeline_details.dag_id}",
                concurrency=pipeline_details.max_active_runs,
                pipelineLocation=pipeline_details.fileloc,
                startDate=pipeline_details.start_date.isoformat()
                if pipeline_details.start_date
                else None,
                tasks=self.get_tasks_from_dag(
                    pipeline_details, self.service_connection.hostPort
                ),
                service=self.context.pipeline_service.fullyQualifiedName.__root__,
                owner=self.get_owner(pipeline_details.owners),
                scheduleInterval=pipeline_details.schedule_interval,
            )
            yield pipeline_request
            self.register_record(pipeline_request=pipeline_request)
        except TypeError as err:
            logger.debug(traceback.format_exc())
            logger.warning(
                f"Error building DAG information from {pipeline_details}. There might be Airflow version"
                f" incompatibilities - {err}"
            )
        except ValidationError as err:
            logger.debug(traceback.format_exc())
            logger.warning(
                f"Error building pydantic model for {pipeline_details} - {err}"
            )
        except Exception as err:
            logger.debug(traceback.format_exc())
            logger.warning(f"Wild error ingesting pipeline {pipeline_details} - {err}")

    def yield_pipeline_lineage_details(
        self, pipeline_details: AirflowDagDetails
    ) -> Optional[Iterable[AddLineageRequest]]:
        """
        Parse xlets and add lineage between Pipelines and Tables
        :param pipeline_details: SerializedDAG from airflow metadata DB
        :return: Lineage from inlets and outlets
        """

        # If the context is not set because of an error upstream,
        # we don't want to continue the processing
        if not self.context.pipeline:
            return

        lineage_details = LineageDetails(
            pipeline=EntityReference(
                id=self.context.pipeline.id.__root__, type="pipeline"
            )
        )

        xlets = get_xlets_from_dag(dag=pipeline_details) if pipeline_details else []
        for xlet in xlets:
            for from_fqn in xlet.inlets or []:
                from_entity = self.metadata.get_by_name(entity=Table, fqn=from_fqn)
                if from_entity:
                    for to_fqn in xlet.outlets or []:
                        to_entity = self.metadata.get_by_name(entity=Table, fqn=to_fqn)
                        if to_entity:
                            lineage = AddLineageRequest(
                                edge=EntitiesEdge(
                                    fromEntity=EntityReference(
                                        id=from_entity.id, type="table"
                                    ),
                                    toEntity=EntityReference(
                                        id=to_entity.id, type="table"
                                    ),
                                    lineageDetails=lineage_details,
                                )
                            )
                            yield lineage
                        else:
                            logger.warning(
                                f"Could not find Table [{to_fqn}] from "
                                f"[{self.context.pipeline.fullyQualifiedName.__root__}] outlets"
                            )
                else:
                    logger.warning(
                        f"Could not find Table [{from_fqn}] from "
                        f"[{self.context.pipeline.fullyQualifiedName.__root__}] inlets"
                    )

    def close(self):
        self.session.close()
