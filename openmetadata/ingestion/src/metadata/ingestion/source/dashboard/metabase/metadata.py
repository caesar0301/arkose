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
"""Metabase source module"""

import traceback
from typing import Iterable, List, Optional

from metadata.generated.schema.api.data.createChart import CreateChartRequest
from metadata.generated.schema.api.data.createDashboard import CreateDashboardRequest
from metadata.generated.schema.api.lineage.addLineage import AddLineageRequest
from metadata.generated.schema.entity.data.chart import Chart
from metadata.generated.schema.entity.data.dashboard import (
    Dashboard as LineageDashboard,
)
from metadata.generated.schema.entity.services.connections.dashboard.metabaseConnection import (
    MetabaseConnection,
)
from metadata.generated.schema.entity.services.connections.metadata.openMetadataConnection import (
    OpenMetadataConnection,
)
from metadata.generated.schema.metadataIngestion.workflow import (
    Source as WorkflowSource,
)
from metadata.ingestion.api.source import InvalidSourceException
from metadata.ingestion.lineage.parser import LineageParser
from metadata.ingestion.lineage.sql_lineage import search_table_entities
from metadata.ingestion.source.dashboard.dashboard_service import DashboardServiceSource
from metadata.ingestion.source.dashboard.metabase.models import (
    MetabaseChart,
    MetabaseCollection,
    MetabaseDashboard,
    MetabaseDashboardDetails,
)
from metadata.utils import fqn
from metadata.utils.filters import filter_by_chart
from metadata.utils.helpers import (
    clean_uri,
    get_standard_chart_type,
    replace_special_with,
)
from metadata.utils.logger import ingestion_logger

logger = ingestion_logger()


class MetabaseSource(DashboardServiceSource):
    """
    Metabase Source Class
    """

    config: WorkflowSource
    metadata_config: OpenMetadataConnection

    @classmethod
    def create(cls, config_dict, metadata_config: OpenMetadataConnection):
        config = WorkflowSource.parse_obj(config_dict)
        connection: MetabaseConnection = config.serviceConnection.__root__.config
        if not isinstance(connection, MetabaseConnection):
            raise InvalidSourceException(
                f"Expected MetabaseConnection, but got {connection}"
            )
        return cls(config, metadata_config)

    def __init__(
        self,
        config: WorkflowSource,
        metadata_config: OpenMetadataConnection,
    ):
        super().__init__(config, metadata_config)
        self.collections: List[MetabaseCollection] = []

    def prepare(self):
        self.collections = self.client.get_collections_list()
        return super().prepare()

    def get_dashboards_list(self) -> Optional[List[MetabaseDashboard]]:
        """
        Get List of all dashboards
        """
        return self.client.get_dashboards_list()

    def get_dashboard_name(self, dashboard: MetabaseDashboard) -> str:
        """
        Get Dashboard Name
        """
        return dashboard.name

    def get_dashboard_details(self, dashboard: MetabaseDashboard) -> dict:
        """
        Get Dashboard Details
        """
        return self.client.get_dashboard_details(dashboard.id)

    def _get_collection_name(self, collection_id: Optional[str]) -> Optional[str]:
        """
        Method to search the dataset using id in the workspace dict
        """
        try:
            if collection_id:
                collection_name = next(
                    (
                        collection.name
                        for collection in self.collections
                        if collection.id == collection_id
                    ),
                    None,
                )
                return collection_name
        except Exception as exc:  # pylint: disable=broad-except
            logger.debug(traceback.format_exc())
            logger.warning(
                f"Error fetching the collection details for [{collection_id}]: {exc}"
            )
        return None

    def yield_dashboard(
        self, dashboard_details: MetabaseDashboardDetails
    ) -> Iterable[CreateDashboardRequest]:
        """
        Method to Get Dashboard Entity
        """
        try:
            dashboard_url = (
                f"{clean_uri(self.service_connection.hostPort)}/dashboard/{dashboard_details.id}-"
                f"{replace_special_with(raw=dashboard_details.name.lower(), replacement='-')}"
            )
            dashboard_request = CreateDashboardRequest(
                name=dashboard_details.id,
                sourceUrl=dashboard_url,
                displayName=dashboard_details.name,
                description=dashboard_details.description,
                project=self._get_collection_name(
                    collection_id=dashboard_details.collection_id
                ),
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
                f"Error creating dashboard [{dashboard_details.name}]: {exc}"
            )

    def yield_dashboard_chart(
        self, dashboard_details: MetabaseDashboardDetails
    ) -> Optional[Iterable[CreateChartRequest]]:
        """Get chart method

        Args:
            dashboard_details:
        Returns:
            Iterable[CreateChartRequest]
        """
        charts = dashboard_details.ordered_cards
        for chart in charts:
            try:
                chart_details = chart.card
                if not chart_details.id or not chart_details.name:
                    continue
                chart_url = (
                    f"{clean_uri(self.service_connection.hostPort)}/question/{chart_details.id}-"
                    f"{replace_special_with(raw=chart_details.name.lower(), replacement='-')}"
                )
                if filter_by_chart(
                    self.source_config.chartFilterPattern, chart_details.name
                ):
                    self.status.filter(chart_details.name, "Chart Pattern not allowed")
                    continue
                yield CreateChartRequest(
                    name=chart_details.id,
                    displayName=chart_details.name,
                    description=chart_details.description,
                    chartType=get_standard_chart_type(chart_details.display).value,
                    sourceUrl=chart_url,
                    service=self.context.dashboard_service.fullyQualifiedName.__root__,
                )
                self.status.scanned(chart_details.name)
            except Exception as exc:  # pylint: disable=broad-except
                logger.debug(traceback.format_exc())
                logger.warning(f"Error creating chart [{chart}]: {exc}")

    def yield_dashboard_lineage_details(
        self,
        dashboard_details: MetabaseDashboardDetails,
        db_service_name: Optional[str],
    ) -> Optional[Iterable[AddLineageRequest]]:
        """Get lineage method

        Args:
            dashboard_details
        """
        if not db_service_name:
            return
        chart_list, dashboard_name = (
            dashboard_details.ordered_cards,
            str(dashboard_details.id),
        )
        for chart in chart_list:
            try:
                chart_details = chart.card
                if (
                    chart_details.dataset_query is None
                    or chart_details.dataset_query.type is None
                ):
                    continue
                if chart_details.dataset_query.type == "native":
                    yield from self._yield_lineage_from_query(
                        chart_details=chart_details,
                        db_service_name=db_service_name,
                        dashboard_name=dashboard_name,
                    ) or []

                # TODO: this method below only gets a single table, but if the chart of type query has a join the other
                # table_ids will be ignored within a nested object
                elif chart_details.dataset_query.type == "query":
                    if not chart_details.table_id:
                        continue
                    yield from self._yield_lineage_from_api(
                        chart_details=chart_details,
                        db_service_name=db_service_name,
                        dashboard_name=dashboard_name,
                    ) or []

            except Exception as exc:  # pylint: disable=broad-except
                logger.debug(traceback.format_exc())
                logger.error(f"Error creating chart [{chart}]: {exc}")

    def _yield_lineage_from_query(
        self, chart_details: MetabaseChart, db_service_name: str, dashboard_name: str
    ) -> Optional[AddLineageRequest]:
        database = self.client.get_database(chart_details.database_id)

        query = None
        if (
            chart_details.dataset_query
            and chart_details.dataset_query.native
            and chart_details.dataset_query.native.query
        ):
            query = chart_details.dataset_query.native.query

        if query is None:
            return

        database_name = database.details.db if database and database.details else None

        lineage_parser = LineageParser(query)
        for table in lineage_parser.source_tables:
            database_schema_name, table = fqn.split(str(table))[-2:]
            database_schema_name = self.check_database_schema_name(database_schema_name)
            from_entities = search_table_entities(
                metadata=self.metadata,
                database=database_name,
                service_name=db_service_name,
                database_schema=database_schema_name,
                table=table,
            )

            to_fqn = fqn.build(
                self.metadata,
                entity_type=LineageDashboard,
                service_name=self.config.serviceName,
                dashboard_name=dashboard_name,
            )
            to_entity = self.metadata.get_by_name(
                entity=LineageDashboard,
                fqn=to_fqn,
            )

            for from_entity in from_entities:
                yield self._get_add_lineage_request(
                    to_entity=to_entity, from_entity=from_entity
                )

    def _yield_lineage_from_api(
        self, chart_details: MetabaseChart, db_service_name: str, dashboard_name: str
    ) -> Optional[AddLineageRequest]:
        table = self.client.get_table(chart_details.table_id)

        if table is None or table.display_name is None:
            return

        database_name = table.db.details.db if table.db and table.db.details else None
        from_entities = search_table_entities(
            metadata=self.metadata,
            database=database_name,
            service_name=db_service_name,
            database_schema=table.table_schema,
            table=table.display_name,
        )

        to_fqn = fqn.build(
            self.metadata,
            entity_type=LineageDashboard,
            service_name=self.config.serviceName,
            dashboard_name=dashboard_name,
        )

        to_entity = self.metadata.get_by_name(
            entity=LineageDashboard,
            fqn=to_fqn,
        )

        for from_entity in from_entities:
            yield self._get_add_lineage_request(
                to_entity=to_entity, from_entity=from_entity
            )
