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
Workflow definition for the ORM Profiler.

- How to specify the source
- How to specify the entities to run
- How to define metrics & tests
"""

from __future__ import annotations

import time
import traceback
from datetime import datetime
from typing import Optional, Union, cast

from pydantic import ValidationError

from metadata.config.common import WorkflowExecutionError
from metadata.data_insight.processor.data_processor import DataProcessor
from metadata.data_insight.processor.entity_report_data_processor import (
    EntityReportDataProcessor,
)
from metadata.data_insight.processor.web_analytic_report_data_processor import (
    WebAnalyticEntityViewReportDataProcessor,
    WebAnalyticUserActivityReportDataProcessor,
)
from metadata.data_insight.runner.kpi_runner import KpiRunner
from metadata.generated.schema.analytics.basic import WebAnalyticEventType
from metadata.generated.schema.analytics.reportData import ReportDataType
from metadata.generated.schema.dataInsight.kpi.kpi import Kpi
from metadata.generated.schema.entity.services.connections.metadata.openMetadataConnection import (
    OpenMetadataConnection,
)
from metadata.generated.schema.entity.services.ingestionPipelines.ingestionPipeline import (
    PipelineState,
)
from metadata.generated.schema.metadataIngestion.workflow import (
    OpenMetadataWorkflowConfig,
    Sink,
)
from metadata.ingestion.api.parser import parse_workflow_config_gracefully
from metadata.ingestion.api.processor import ProcessorStatus
from metadata.ingestion.api.workflow import REPORTS_INTERVAL_SECONDS
from metadata.ingestion.ometa.ometa_api import EntityList, OpenMetadata
from metadata.ingestion.sink.elasticsearch import ElasticsearchSink
from metadata.timer.repeated_timer import RepeatedTimer
from metadata.timer.workflow_reporter import get_ingestion_status_timer
from metadata.utils.importer import get_sink
from metadata.utils.logger import data_insight_logger, set_loggers_level
from metadata.utils.time_utils import get_beginning_of_day_timestamp_mill
from metadata.utils.workflow_output_handler import print_data_insight_status
from metadata.workflow.workflow_status_mixin import WorkflowStatusMixin

logger = data_insight_logger()

NOW = datetime.utcnow().timestamp() * 1000
RETENTION_DAYS = 7


class DataInsightWorkflow(WorkflowStatusMixin):
    """
    Configure and run the Data Insigt workflow

    Attributes:
    """

    def __init__(self, config: OpenMetadataWorkflowConfig) -> None:
        self.config = config
        self._timer: Optional[RepeatedTimer] = None

        set_loggers_level(config.workflowConfig.loggerLevel.value)

        self.metadata_config: OpenMetadataConnection = (
            self.config.workflowConfig.openMetadataServerConfig
        )
        self.metadata = OpenMetadata(self.metadata_config)
        self.set_ingestion_pipeline_status(state=PipelineState.running)

        self.status = ProcessorStatus()
        self.source: Optional[
            Union[
                DataProcessor,
                EntityReportDataProcessor,
                WebAnalyticEntityViewReportDataProcessor,
                WebAnalyticUserActivityReportDataProcessor,
            ]
        ] = None

        self.kpi_runner: Optional[KpiRunner] = None

        if self.config.sink:
            self.sink = get_sink(
                sink_type="metadata-rest",
                sink_config=Sink(type="metadata-rest", config={}),  # type: ignore
                metadata_config=self.metadata_config,
                from_="data_insight",
            )

            self.es_sink = get_sink(
                sink_type=self.config.sink.type,
                sink_config=self.config.sink,
                metadata_config=self.metadata_config,
                from_="ingestion",
            )

            self.es_sink = cast(ElasticsearchSink, self.es_sink)

    @property
    def timer(self) -> RepeatedTimer:
        """Status timer"""
        if not self._timer:
            self._timer = get_ingestion_status_timer(
                interval=REPORTS_INTERVAL_SECONDS, logger=logger, workflow=self
            )

        return self._timer

    @staticmethod
    def _is_kpi_active(entity: Kpi) -> bool:
        """Check if a KPI is active

        Args:
            entity (Kpi): KPI entity

        Returns:
            Kpi:
        """

        start_date = entity.startDate.__root__
        end_date = entity.endDate.__root__

        if not start_date or not end_date:
            logger.warning(
                f"Start date or End date was not defined.\n\t-startDate: {start_date}\n\t-end_date: {end_date}\n"
                "We won't be running the KPI validation"
            )
            return False

        if start_date <= NOW <= end_date:
            return True

        return False

    def _get_kpis(self) -> list[Kpi]:
        """get the list of KPIs and return the active ones

        Returns:
            _type_: _description_
        """

        kpis: EntityList[Kpi] = self.metadata.list_entities(
            entity=Kpi, fields="*"  # type: ignore
        )

        return [kpi for kpi in kpis.entities if self._is_kpi_active(kpi)]

    def _execute_data_processor(self):
        """Data processor method to refine raw data into report data and ingest it in ES"""
        for report_data_type in ReportDataType:
            logger.info(f"Processing data for report type {report_data_type}")
            try:
                self.source = DataProcessor.create(
                    _data_processor_type=report_data_type.value, metadata=self.metadata
                )
                for record in self.source.process():
                    if hasattr(self, "sink"):
                        self.sink.write_record(record)
                    if hasattr(self, "es_sink"):
                        self.es_sink.write_record(record)
                    else:
                        logger.warning(
                            "No sink attribute found, skipping ingestion of KPI result"
                        )
                self.status.records.extend(self.source.processor_status.records)
                self.status.failures.extend(self.source.processor_status.failures)
                self.status.warnings.extend(self.source.processor_status.warnings)

            except Exception as exc:
                error = f"Error while executing data insight workflow for report type {report_data_type}: {exc}"
                logger.error(error)
                logger.debug(traceback.format_exc())
                self.status.failed(str(report_data_type), error, traceback.format_exc())

    def _execute_kpi_runner(self):
        """KPI runner method to run KPI definiton against platform latest metric"""
        kpis = self._get_kpis()
        self.kpi_runner = KpiRunner(kpis, self.metadata)

        for kpi_result in self.kpi_runner.run():
            if hasattr(self, "sink"):
                self.sink.write_record(kpi_result)
            else:
                logger.warning(
                    "No sink attribute found, skipping ingestion of KPI result"
                )

    def _execute_web_analytics_event_data_cleaning(self):
        """We will delete web analytics events older than `RETENTION_DAYS`
        to limit its accumulation
        """
        tmsp = get_beginning_of_day_timestamp_mill(days=RETENTION_DAYS)
        for web_analytic_event in WebAnalyticEventType:
            self.metadata.delete_web_analytic_event_before_ts_exclusive(
                web_analytic_event,
                tmsp,
            )

    @classmethod
    def create(cls, config_dict: dict) -> DataInsightWorkflow:
        """instantiate a class object

        Args:
            config_dict (dict): workflow config

        Raises:
            err: wrong config

        Returns:
            DataInsightWorkflow
        """
        try:
            config = parse_workflow_config_gracefully(config_dict)
            config = cast(OpenMetadataWorkflowConfig, config)  # for static type checked
            return cls(config)
        except ValidationError as err:
            logger.debug(traceback.format_exc())
            logger.error(
                f"Error trying to parse the Profiler Workflow configuration: {err}"
            )
            raise err

    def execute(self):
        """Execute workflow"""
        self.timer.trigger()

        try:
            logger.info("Starting data processor execution")
            self._execute_data_processor()
            logger.info("Data processor finished running")

            logger.info("Sleeping for 1 second. Waiting for ES data to be indexed.")
            time.sleep(1)
            logger.info("Starting KPI runner")
            self._execute_kpi_runner()
            logger.info("KPI runner finished running")

            logger.info(f"Deleting Web Analytic Events older than {RETENTION_DAYS}")
            self._execute_web_analytics_event_data_cleaning()

            # At the end of the `execute`, update the associated Ingestion Pipeline status as success
            self.set_ingestion_pipeline_status(PipelineState.success)
        # Any unhandled exception breaking the workflow should update the status
        except Exception as err:
            self.set_ingestion_pipeline_status(PipelineState.failed)
            raise err
        finally:
            self.stop()

    def _raise_from_status_internal(self, raise_warnings=False):
        if self.source and self.source.get_status().failures:
            raise WorkflowExecutionError(
                "Source reported errors", self.source.get_status()
            )
        if hasattr(self, "sink") and self.sink.get_status().failures:
            raise WorkflowExecutionError("Sink reported errors", self.sink.get_status())
        if raise_warnings and (
            (self.source and self.source.get_status().warnings)
            or self.sink.get_status().warnings
        ):
            raise WorkflowExecutionError(
                "Source reported warnings",
                self.source.get_status() if self.source else None,
            )

    def print_status(self) -> None:
        print_data_insight_status(self)

    def result_status(self) -> int:
        """
        Returns 1 if status is failed, 0 otherwise.
        """
        if (
            (self.source and self.source.get_status().failures)
            or self.status.failures
            or (hasattr(self, "sink") and self.sink.get_status().failures)
        ):
            return 1
        return 0

    def stop(self):
        """
        Close all connections
        """
        self.metadata.close()
        self.timer.stop()
