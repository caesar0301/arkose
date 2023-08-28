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
"""Qlik Sense Source Module"""

import traceback
from typing import Iterable, List, Optional

from metadata.generated.schema.api.data.createChart import CreateChartRequest
from metadata.generated.schema.api.data.createDashboard import CreateDashboardRequest
from metadata.generated.schema.api.data.createDashboardDataModel import (
    CreateDashboardDataModelRequest,
)
from metadata.generated.schema.api.lineage.addLineage import AddLineageRequest
from metadata.generated.schema.entity.data.chart import Chart, ChartType
from metadata.generated.schema.entity.data.dashboardDataModel import (
    DashboardDataModel,
    DataModelType,
)
from metadata.generated.schema.entity.data.table import Column, DataType, Table
from metadata.generated.schema.entity.services.connections.dashboard.qlikSenseConnection import (
    QlikSenseConnection,
)
from metadata.generated.schema.entity.services.connections.metadata.openMetadataConnection import (
    OpenMetadataConnection,
)
from metadata.generated.schema.entity.services.dashboardService import (
    DashboardServiceType,
)
from metadata.generated.schema.entity.services.databaseService import DatabaseService
from metadata.generated.schema.metadataIngestion.workflow import (
    Source as WorkflowSource,
)
from metadata.ingestion.api.source import InvalidSourceException
from metadata.ingestion.source.dashboard.dashboard_service import DashboardServiceSource
from metadata.ingestion.source.dashboard.qliksense.client import QlikSenseClient
from metadata.ingestion.source.dashboard.qliksense.models import (
    QlikDashboard,
    QlikTable,
)
from metadata.utils import fqn
from metadata.utils.filters import filter_by_chart, filter_by_datamodel
from metadata.utils.helpers import clean_uri
from metadata.utils.logger import ingestion_logger

logger = ingestion_logger()


class QliksenseSource(DashboardServiceSource):
    """
    Qlik Sense Source Class
    """

    config: WorkflowSource
    client: QlikSenseClient
    metadata_config: OpenMetadataConnection

    @classmethod
    def create(cls, config_dict, metadata_config: OpenMetadataConnection):
        config = WorkflowSource.parse_obj(config_dict)
        connection: QlikSenseConnection = config.serviceConnection.__root__.config
        if not isinstance(connection, QlikSenseConnection):
            raise InvalidSourceException(
                f"Expected QlikSenseConnection, but got {connection}"
            )
        return cls(config, metadata_config)

    def __init__(
        self,
        config: WorkflowSource,
        metadata_config: OpenMetadataConnection,
    ):
        super().__init__(config, metadata_config)
        self.collections: List[QlikDashboard] = []
        # Data models will be cleared up for each dashboard
        self.data_models: List[QlikTable] = []

    def get_dashboards_list(self) -> Iterable[QlikDashboard]:
        """
        Get List of all dashboards
        """
        for dashboard in self.client.get_dashboards_list():
            # create app specific websocket
            self.client.connect_websocket(dashboard.qDocId)
            # clean data models for next iteration
            self.data_models = []
            yield dashboard

    def get_dashboard_name(self, dashboard: QlikDashboard) -> str:
        """
        Get Dashboard Name
        """
        return dashboard.qDocName

    def get_dashboard_details(self, dashboard: QlikDashboard) -> dict:
        """
        Get Dashboard Details
        """
        return dashboard

    def yield_dashboard(
        self, dashboard_details: QlikDashboard
    ) -> Iterable[CreateDashboardRequest]:
        """
        Method to Get Dashboard Entity
        """
        try:
            if self.service_connection.displayUrl:
                dashboard_url = (
                    f"{clean_uri(self.service_connection.displayUrl)}/sense/app/"
                    f"{dashboard_details.qDocId}/overview"
                )
            else:
                dashboard_url = None

            dashboard_request = CreateDashboardRequest(
                name=dashboard_details.qDocId,
                sourceUrl=dashboard_url,
                displayName=dashboard_details.qDocName,
                description=dashboard_details.qMeta.description,
                charts=[
                    fqn.build(
                        self.metadata,
                        entity_type=Chart,
                        service_name=self.context.dashboard_service.fullyQualifiedName.__root__,
                        chart_name=chart.name.__root__,
                    )
                    for chart in self.context.charts
                ],
                service=self.context.dashboard_service.fullyQualifiedName.__root__,
            )
            yield dashboard_request
            self.register_record(dashboard_request=dashboard_request)
        except Exception as exc:  # pylint: disable=broad-except
            logger.debug(traceback.format_exc())
            logger.warning(
                f"Error creating dashboard [{dashboard_details.qDocName}]: {exc}"
            )

    def yield_dashboard_chart(
        self, dashboard_details: QlikDashboard
    ) -> Iterable[CreateChartRequest]:
        """Get chart method

        Args:
            dashboard_details:
        Returns:
            Iterable[CreateChartRequest]
        """
        charts = self.client.get_dashboard_charts(dashboard_id=dashboard_details.qDocId)
        for chart in charts:
            try:
                if not chart.qInfo.qId:
                    continue
                if self.service_connection.displayUrl:
                    chart_url = (
                        f"{clean_uri(self.service_connection.displayUrl)}/sense/app/{dashboard_details.qDocId}"
                        f"/sheet/{chart.qInfo.qId}"
                    )
                else:
                    chart_url = None
                if chart.qMeta.title and filter_by_chart(
                    self.source_config.chartFilterPattern, chart.qMeta.title
                ):
                    self.status.filter(chart.qMeta.title, "Chart Pattern not allowed")
                    continue
                yield CreateChartRequest(
                    name=chart.qInfo.qId,
                    displayName=chart.qMeta.title,
                    description=chart.qMeta.description,
                    chartType=ChartType.Other,
                    sourceUrl=chart_url,
                    service=self.context.dashboard_service.fullyQualifiedName.__root__,
                )
                self.status.scanned(chart.qMeta.title)
            except Exception as exc:  # pylint: disable=broad-except
                logger.debug(traceback.format_exc())
                logger.warning(f"Error creating chart [{chart}]: {exc}")

    def get_column_info(self, data_source: QlikTable) -> Optional[List[Column]]:
        """
        Args:
            data_source: DataSource
        Returns:
            Columns details for Data Model
        """
        datasource_columns = []
        for field in data_source.fields or []:
            try:
                parsed_fields = {
                    "dataTypeDisplay": "Qlik Field",
                    "dataType": DataType.UNKNOWN,
                    "name": field.id,
                    "displayName": field.name if field.name else field.id,
                }
                datasource_columns.append(Column(**parsed_fields))
            except Exception as exc:
                logger.debug(traceback.format_exc())
                logger.warning(f"Error to yield datamodel column: {exc}")
        return datasource_columns

    def yield_datamodel(self, _: QlikDashboard):
        if self.source_config.includeDataModels:
            self.data_models = self.client.get_dashboard_models()
            for data_model in self.data_models or []:
                try:
                    data_model_name = (
                        data_model.tableName if data_model.tableName else data_model.id
                    )
                    if filter_by_datamodel(
                        self.source_config.dataModelFilterPattern, data_model_name
                    ):
                        self.status.filter(data_model_name, "Data model filtered out.")
                        continue

                    data_model_request = CreateDashboardDataModelRequest(
                        name=data_model.id,
                        displayName=data_model_name,
                        service=self.context.dashboard_service.fullyQualifiedName.__root__,
                        dataModelType=DataModelType.QlikSenseDataModel.value,
                        serviceType=DashboardServiceType.QlikSense.value,
                        columns=self.get_column_info(data_model),
                    )
                    yield data_model_request
                    self.status.scanned(
                        f"Data Model Scanned: {data_model_request.displayName}"
                    )
                except Exception as exc:
                    error_msg = f"Error yielding Data Model [{data_model_name}]: {exc}"
                    self.status.failed(
                        name=data_model_name,
                        error=error_msg,
                        stack_trace=traceback.format_exc(),
                    )
                    logger.error(error_msg)
                    logger.debug(traceback.format_exc())

    def _get_datamodel(self, datamodel: QlikTable):
        datamodel_fqn = fqn.build(
            self.metadata,
            entity_type=DashboardDataModel,
            service_name=self.context.dashboard_service.fullyQualifiedName.__root__,
            data_model_name=datamodel.id,
        )
        if datamodel_fqn:
            return self.metadata.get_by_name(
                entity=DashboardDataModel,
                fqn=datamodel_fqn,
            )
        return None

    def _get_database_table(
        self, db_service_entity: DatabaseService, datamodel: QlikTable
    ) -> Optional[Table]:
        """
        Get the table entity for lineage
        """
        # table.name in tableau can come as db.schema.table_name. Hence the logic to split it
        if datamodel.tableName and db_service_entity:
            try:
                if len(datamodel.connectorProperties.tableQualifiers) > 1:
                    (
                        database_name,
                        schema_name,
                    ) = datamodel.connectorProperties.tableQualifiers[-2:]
                elif len(datamodel.connectorProperties.tableQualifiers) == 1:
                    schema_name = datamodel.connectorProperties.tableQualifiers[-1]
                    database_name = None
                else:
                    schema_name, database_name = None, None

                table_fqn = fqn.build(
                    self.metadata,
                    entity_type=Table,
                    service_name=db_service_entity.name.__root__,
                    schema_name=schema_name,
                    table_name=datamodel.tableName,
                    database_name=database_name,
                )
                if table_fqn:
                    return self.metadata.get_by_name(
                        entity=Table,
                        fqn=table_fqn,
                    )
            except Exception as exc:
                logger.debug(traceback.format_exc())
                logger.warning(f"Error occured while finding table fqn: {exc}")
        return None

    def yield_dashboard_lineage_details(
        self,
        dashboard_details: QlikDashboard,
        db_service_name: Optional[str],
    ) -> Iterable[AddLineageRequest]:
        """Get lineage method

        Args:
            dashboard_details
        """
        db_service_entity = self.metadata.get_by_name(
            entity=DatabaseService, fqn=db_service_name
        )
        for datamodel in self.data_models or []:
            try:
                data_model_entity = self._get_datamodel(datamodel=datamodel)
                if data_model_entity:
                    om_table = self._get_database_table(
                        db_service_entity, datamodel=datamodel
                    )
                    if om_table:
                        yield self._get_add_lineage_request(
                            to_entity=data_model_entity, from_entity=om_table
                        )
            except Exception as err:
                logger.debug(traceback.format_exc())
                logger.error(
                    f"Error to yield dashboard lineage details for DB service name [{db_service_name}]: {err}"
                )

    def close(self):
        self.client.close_websocket()
        return super().close()
