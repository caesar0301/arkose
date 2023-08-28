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
Superset source module
"""

import traceback
from typing import Iterable, Optional

from sqlalchemy import sql
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import make_url

from metadata.generated.schema.api.data.createChart import CreateChartRequest
from metadata.generated.schema.api.data.createDashboard import CreateDashboardRequest
from metadata.generated.schema.api.data.createDashboardDataModel import (
    CreateDashboardDataModelRequest,
)
from metadata.generated.schema.entity.data.chart import Chart
from metadata.generated.schema.entity.data.dashboardDataModel import DataModelType
from metadata.generated.schema.entity.data.table import Table
from metadata.generated.schema.entity.services.connections.metadata.openMetadataConnection import (
    OpenMetadataConnection,
)
from metadata.generated.schema.entity.services.databaseService import DatabaseService
from metadata.generated.schema.metadataIngestion.workflow import (
    Source as WorkflowSource,
)
from metadata.ingestion.source.dashboard.superset.mixin import SupersetSourceMixin
from metadata.ingestion.source.dashboard.superset.models import (
    FetchChart,
    FetchColumn,
    FetchDashboard,
)
from metadata.ingestion.source.dashboard.superset.queries import (
    FETCH_ALL_CHARTS,
    FETCH_COLUMN,
    FETCH_DASHBOARDS,
)
from metadata.utils import fqn
from metadata.utils.filters import filter_by_datamodel
from metadata.utils.helpers import (
    clean_uri,
    get_database_name_for_lineage,
    get_standard_chart_type,
)
from metadata.utils.logger import ingestion_logger

logger = ingestion_logger()


class SupersetDBSource(SupersetSourceMixin):
    """
    Superset DB Source Class
    """

    def __init__(self, config: WorkflowSource, metadata_config: OpenMetadataConnection):
        super().__init__(config, metadata_config)
        self.engine: Engine = self.client

    def prepare(self):
        """
        Fetching all charts available in superset
        this step is done because fetch_total_charts api fetches all
        the required information which is not available in fetch_charts_with_id api
        """
        charts = self.engine.execute(FETCH_ALL_CHARTS)
        for chart in charts:
            chart_detail = FetchChart(**chart)
            self.all_charts[chart_detail.id] = chart_detail

    def get_column_list(self, table_name: FetchChart) -> Optional[Iterable[FetchChart]]:
        sql_query = sql.text(FETCH_COLUMN.format(table_name=table_name.lower()))
        col_list = self.engine.execute(sql_query)
        return [FetchColumn(**col) for col in col_list]

    def get_dashboards_list(self) -> Optional[Iterable[FetchDashboard]]:
        """
        Get List of all dashboards
        """
        dashboards = self.engine.execute(FETCH_DASHBOARDS)
        for dashboard in dashboards:
            yield FetchDashboard(**dashboard)

    def yield_dashboard(
        self, dashboard_details: FetchDashboard
    ) -> Optional[Iterable[CreateDashboardRequest]]:
        """
        Method to Get Dashboard Entity
        """
        dashboard_request = CreateDashboardRequest(
            name=dashboard_details.id,
            displayName=dashboard_details.dashboard_title,
            sourceUrl=f"{clean_uri(self.service_connection.hostPort)}/superset/dashboard/{dashboard_details.id}/",
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

    def _get_datasource_fqn_for_lineage(
        self, chart_json: FetchChart, db_service_entity: DatabaseService
    ):
        return (
            self._get_datasource_fqn(db_service_entity, chart_json)
            if chart_json.table_name
            else None
        )

    def yield_dashboard_chart(
        self, dashboard_details: FetchDashboard
    ) -> Optional[Iterable[CreateChartRequest]]:
        """
        Metod to fetch charts linked to dashboard
        """
        for chart_id in self._get_charts_of_dashboard(dashboard_details):
            chart_json = self.all_charts.get(chart_id)
            if not chart_json:
                logger.warning(f"chart details for id: {chart_id} not found, skipped")
                continue
            chart = CreateChartRequest(
                name=chart_json.id,
                displayName=chart_json.slice_name,
                description=chart_json.description,
                chartType=get_standard_chart_type(chart_json.viz_type),
                sourceUrl=f"{clean_uri(self.service_connection.hostPort)}/explore/?slice_id={chart_json.id}",
                service=self.context.dashboard_service.fullyQualifiedName.__root__,
            )
            yield chart

    def _get_database_name(
        self, sqa_str: str, db_service_entity: DatabaseService
    ) -> Optional[str]:
        default_db_name = None
        if sqa_str:
            sqa_url = make_url(sqa_str)
            default_db_name = sqa_url.database if sqa_url else None
        return get_database_name_for_lineage(db_service_entity, default_db_name)

    def _get_datasource_fqn(
        self, db_service_entity: DatabaseService, chart_json: FetchChart
    ) -> Optional[str]:
        try:
            dataset_fqn = fqn.build(
                self.metadata,
                entity_type=Table,
                table_name=chart_json.table_name,
                database_name=self._get_database_name(
                    chart_json.sqlalchemy_uri, db_service_entity
                ),
                schema_name=chart_json.table_schema,
                service_name=db_service_entity.name.__root__,
            )
            return dataset_fqn
        except Exception as err:
            logger.debug(traceback.format_exc())
            logger.warning(
                f"Failed to fetch Datasource with id [{chart_json.table_name}]: {err}"
            )
        return None

    def yield_datamodel(
        self, dashboard_details: FetchDashboard
    ) -> Optional[Iterable[CreateDashboardDataModelRequest]]:

        if self.source_config.includeDataModels:
            for chart_id in self._get_charts_of_dashboard(dashboard_details):
                chart_json = self.all_charts.get(chart_id)
                if not chart_json:
                    logger.warning(
                        f"chart details for id: {chart_id} not found, skipped"
                    )
                    continue
                if filter_by_datamodel(
                    self.source_config.dataModelFilterPattern, chart_json.table_name
                ):
                    self.status.filter(
                        chart_json.table_name, "Data model filtered out."
                    )
                col_names = self.get_column_list(chart_json.table_name)
                try:
                    data_model_request = CreateDashboardDataModelRequest(
                        name=chart_json.datasource_id,
                        displayName=chart_json.table_name,
                        service=self.context.dashboard_service.fullyQualifiedName.__root__,
                        columns=self.get_column_info(col_names),
                        dataModelType=DataModelType.SupersetDataModel.value,
                    )
                    yield data_model_request
                    self.status.scanned(
                        f"Data Model Scanned: {data_model_request.displayName}"
                    )
                except Exception as exc:
                    error_msg = (
                        f"Error yielding Data Model [{chart_json.table_name}]: {exc}"
                    )
                    self.status.failed(
                        name=chart_json.datasource_id,
                        error=error_msg,
                        stack_trace=traceback.format_exc(),
                    )
                    logger.error(error_msg)
                    logger.debug(traceback.format_exc())
