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
Redshift source ingestion
"""

import re
import traceback
from typing import Iterable, List, Optional, Tuple

from sqlalchemy import inspect, sql
from sqlalchemy.dialects.postgresql.base import PGDialect
from sqlalchemy.engine.reflection import Inspector
from sqlalchemy_redshift.dialect import RedshiftDialect, RedshiftDialectMixin

from metadata.generated.schema.entity.data.database import Database
from metadata.generated.schema.entity.data.table import (
    ConstraintType,
    IntervalType,
    TableConstraint,
    TablePartition,
    TableType,
)
from metadata.generated.schema.entity.services.connections.database.redshiftConnection import (
    RedshiftConnection,
)
from metadata.generated.schema.entity.services.connections.metadata.openMetadataConnection import (
    OpenMetadataConnection,
)
from metadata.generated.schema.metadataIngestion.workflow import (
    Source as WorkflowSource,
)
from metadata.ingestion.api.source import InvalidSourceException
from metadata.ingestion.source.database.common_db_source import (
    CommonDbSourceService,
    TableNameAndType,
)
from metadata.ingestion.source.database.redshift.queries import (
    REDSHIFT_GET_ALL_RELATION_INFO,
    REDSHIFT_GET_DATABASE_NAMES,
    REDSHIFT_PARTITION_DETAILS,
)
from metadata.ingestion.source.database.redshift.utils import (
    _get_all_relation_info,
    _get_column_info,
    _get_pg_column_info,
    _get_schema_column_info,
    get_columns,
    get_table_comment,
)
from metadata.utils import fqn
from metadata.utils.filters import filter_by_database
from metadata.utils.logger import ingestion_logger
from metadata.utils.sqlalchemy_utils import get_all_table_comments

logger = ingestion_logger()


STANDARD_TABLE_TYPES = {
    "r": TableType.Regular,
    "e": TableType.External,
    "v": TableType.View,
}


RedshiftDialectMixin._get_column_info = (  # pylint: disable=protected-access
    _get_column_info
)
RedshiftDialectMixin._get_schema_column_info = (  # pylint: disable=protected-access
    _get_schema_column_info
)
RedshiftDialectMixin.get_columns = get_columns
PGDialect._get_column_info = _get_pg_column_info  # pylint: disable=protected-access
RedshiftDialect.get_all_table_comments = get_all_table_comments
RedshiftDialect.get_table_comment = get_table_comment
RedshiftDialect._get_all_relation_info = (  # pylint: disable=protected-access
    _get_all_relation_info
)


class RedshiftSource(CommonDbSourceService):
    """
    Implements the necessary methods to extract
    Database metadata from Redshift Source
    """

    def __init__(self, config, metadata_config):
        super().__init__(config, metadata_config)
        self.partition_details = {}

    @classmethod
    def create(cls, config_dict, metadata_config: OpenMetadataConnection):
        config: WorkflowSource = WorkflowSource.parse_obj(config_dict)
        connection: RedshiftConnection = config.serviceConnection.__root__.config
        if not isinstance(connection, RedshiftConnection):
            raise InvalidSourceException(
                f"Expected RedshiftConnection, but got {connection}"
            )
        return cls(config, metadata_config)

    def get_partition_details(self) -> None:
        """
        Populate partition details
        """
        try:
            self.partition_details.clear()
            results = self.engine.execute(REDSHIFT_PARTITION_DETAILS).fetchall()
            for row in results:
                self.partition_details[f"{row.schema}.{row.table}"] = row.diststyle
        except Exception as exe:
            logger.debug(traceback.format_exc())
            logger.debug(f"Failed to fetch partition details due: {exe}")

    def query_table_names_and_types(
        self, schema_name: str
    ) -> Iterable[TableNameAndType]:
        """
        Handle custom table types
        """

        result = self.connection.execute(
            sql.text(REDSHIFT_GET_ALL_RELATION_INFO),
            {"schema": schema_name},
        )

        return [
            TableNameAndType(
                name=name, type_=STANDARD_TABLE_TYPES.get(relkind, TableType.Regular)
            )
            for name, relkind in result
        ]

    def get_database_names(self) -> Iterable[str]:
        if not self.config.serviceConnection.__root__.config.ingestAllDatabases:
            self.inspector = inspect(self.engine)
            self.get_partition_details()
            yield self.config.serviceConnection.__root__.config.database
        else:
            results = self.connection.execute(REDSHIFT_GET_DATABASE_NAMES)
            for res in results:
                row = list(res)
                new_database = row[0]
                database_fqn = fqn.build(
                    self.metadata,
                    entity_type=Database,
                    service_name=self.context.database_service.name.__root__,
                    database_name=new_database,
                )

                if filter_by_database(
                    self.source_config.databaseFilterPattern,
                    database_fqn
                    if self.source_config.useFqnForFiltering
                    else new_database,
                ):
                    self.status.filter(database_fqn, "Database Filtered Out")
                    continue

                try:
                    self.set_inspector(database_name=new_database)
                    self.get_partition_details()
                    yield new_database
                except Exception as exc:
                    logger.debug(traceback.format_exc())
                    logger.error(
                        f"Error trying to connect to database {new_database}: {exc}"
                    )

    def _get_partition_key(self, diststyle: str) -> Optional[List[str]]:
        try:
            regex = re.match(r"KEY\((\w+)\)", diststyle)
            if regex:
                return [regex.group(1)]
        except Exception as err:
            logger.debug(traceback.format_exc())
            logger.warning(err)
        return None

    def get_table_partition_details(
        self, table_name: str, schema_name: str, inspector: Inspector
    ) -> Tuple[bool, TablePartition]:
        diststyle = self.partition_details.get(f"{schema_name}.{table_name}")
        if diststyle:
            partition_details = TablePartition(
                columns=self._get_partition_key(diststyle),
                intervalType=IntervalType.COLUMN_VALUE,
            )
            return True, partition_details
        return False, None

    def process_additional_table_constraints(
        self, column: dict, table_constraints: List[TableConstraint]
    ) -> None:
        """
        Process DIST_KEY & SORT_KEY column properties
        """

        if column.get("distkey"):
            table_constraints.append(
                TableConstraint(
                    constraintType=ConstraintType.DIST_KEY,
                    columns=[column.get("name")],
                )
            )

        if column.get("sortkey"):
            table_constraints.append(
                TableConstraint(
                    constraintType=ConstraintType.SORT_KEY,
                    columns=[column.get("name")],
                )
            )
