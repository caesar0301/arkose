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
Databricks Unity Catalog Lineage Source Module
"""
from typing import Iterable, Optional

from metadata.generated.schema.api.lineage.addLineage import AddLineageRequest
from metadata.generated.schema.entity.data.database import Database
from metadata.generated.schema.entity.data.table import Table
from metadata.generated.schema.entity.services.connections.database.databricksConnection import (
    DatabricksConnection,
)
from metadata.generated.schema.entity.services.connections.metadata.openMetadataConnection import (
    OpenMetadataConnection,
)
from metadata.generated.schema.metadataIngestion.workflow import (
    Source as WorkflowSource,
)
from metadata.generated.schema.type.entityLineage import (
    ColumnLineage,
    EntitiesEdge,
    LineageDetails,
)
from metadata.generated.schema.type.entityReference import EntityReference
from metadata.ingestion.api.source import InvalidSourceException, Source
from metadata.ingestion.lineage.sql_lineage import get_column_fqn
from metadata.ingestion.ometa.ometa_api import OpenMetadata
from metadata.ingestion.source.connections import get_test_connection_fn
from metadata.ingestion.source.database.databricks.client import DatabricksClient
from metadata.ingestion.source.database.databricks.connection import get_connection
from metadata.ingestion.source.database.databricks.models import LineageTableStreams
from metadata.utils import fqn
from metadata.utils.logger import ingestion_logger

logger = ingestion_logger()


class DatabricksUnityCatalogLineageSource(Source[AddLineageRequest]):
    """
    Databricks Lineage Unity Catalog Source
    """

    def __init__(
        self,
        config: WorkflowSource,
        metadata_config: OpenMetadataConnection,
    ):
        super().__init__()
        self.config = config
        self.metadata_config = metadata_config
        self.metadata = OpenMetadata(metadata_config)
        self.service_connection = self.config.serviceConnection.__root__.config
        self.source_config = self.config.sourceConfig.config
        self.client = DatabricksClient(self.service_connection)
        self.connection_obj = get_connection(self.service_connection)
        self.test_connection()

    def close(self):
        """
        By default, there is nothing to close
        """

    def prepare(self):
        """
        By default, there's nothing to prepare
        """

    @classmethod
    def create(cls, config_dict, metadata_config: OpenMetadataConnection):
        """Create class instance"""
        config: WorkflowSource = WorkflowSource.parse_obj(config_dict)
        connection: DatabricksConnection = config.serviceConnection.__root__.config
        if not isinstance(connection, DatabricksConnection):
            raise InvalidSourceException(
                f"Expected DatabricksConnection, but got {connection}"
            )
        return cls(config, metadata_config)

    def _get_lineage_details(
        self, from_table: Table, to_table: Table, databricks_table_fqn: str
    ) -> Optional[LineageDetails]:
        col_lineage = []
        for column in to_table.columns:
            column_streams = self.client.get_column_lineage(
                databricks_table_fqn, column_name=column.name.__root__
            )
            from_columns = []
            for col in column_streams.upstream_cols:
                col_fqn = get_column_fqn(from_table, col.name)
                if col_fqn:
                    from_columns.append(col_fqn)

            if from_columns:
                col_lineage.append(
                    ColumnLineage(
                        fromColumns=from_columns,
                        toColumn=column.fullyQualifiedName.__root__,
                    )
                )
        if col_lineage:
            return LineageDetails(columnsLineage=col_lineage)
        return None

    def next_record(self) -> Iterable[AddLineageRequest]:
        """
        Based on the query logs, prepare the lineage
        and send it to the sink
        """

        for database in self.metadata.list_all_entities(
            entity=Database, params={"service": self.config.serviceName}
        ):
            for table in self.metadata.list_all_entities(
                entity=Table, params={"database": database.fullyQualifiedName.__root__}
            ):
                databricks_table_fqn = f"{table.database.name}.{table.databaseSchema.name}.{table.name.__root__}"
                table_streams: LineageTableStreams = self.client.get_table_lineage(
                    databricks_table_fqn
                )
                for upstream_table in table_streams.upstream_tables:
                    from_entity_fqn = fqn.build(
                        metadata=self.metadata,
                        entity_type=Table,
                        database_name=upstream_table.catalog_name,
                        schema_name=upstream_table.schema_name,
                        table_name=upstream_table.name,
                        service_name=self.config.serviceName,
                    )

                    from_entity = self.metadata.get_by_name(
                        entity=Table, fqn=from_entity_fqn
                    )
                    if from_entity:
                        lineage_details = self._get_lineage_details(
                            from_table=from_entity,
                            to_table=table,
                            databricks_table_fqn=databricks_table_fqn,
                        )
                        yield AddLineageRequest(
                            edge=EntitiesEdge(
                                toEntity=EntityReference(id=table.id, type="table"),
                                fromEntity=EntityReference(
                                    id=from_entity.id, type="table"
                                ),
                                lineageDetails=lineage_details,
                            )
                        )

    def test_connection(self) -> None:
        test_connection_fn = get_test_connection_fn(self.service_connection)
        test_connection_fn(self.metadata, self.connection_obj, self.service_connection)
