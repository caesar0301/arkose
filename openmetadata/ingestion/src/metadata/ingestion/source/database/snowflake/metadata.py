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
Snowflake source module
"""
import json
import traceback
from typing import Iterable, List, Optional, Tuple

import sqlparse
from snowflake.sqlalchemy.custom_types import VARIANT
from snowflake.sqlalchemy.snowdialect import SnowflakeDialect, ischema_names
from sqlalchemy.engine.reflection import Inspector
from sqlparse.sql import Function, Identifier

from metadata.generated.schema.entity.data.database import Database
from metadata.generated.schema.entity.data.table import (
    IntervalType,
    TablePartition,
    TableType,
)
from metadata.generated.schema.entity.services.connections.database.snowflakeConnection import (
    SnowflakeConnection,
)
from metadata.generated.schema.entity.services.connections.metadata.openMetadataConnection import (
    OpenMetadataConnection,
)
from metadata.generated.schema.metadataIngestion.workflow import (
    Source as WorkflowSource,
)
from metadata.ingestion.api.source import InvalidSourceException
from metadata.ingestion.models.ometa_classification import OMetaTagAndClassification
from metadata.ingestion.source.database.column_type_parser import create_sqlalchemy_type
from metadata.ingestion.source.database.common_db_source import (
    CommonDbSourceService,
    TableNameAndType,
)
from metadata.ingestion.source.database.snowflake.constants import (
    SNOWFLAKE_REGION_ID_MAP,
)
from metadata.ingestion.source.database.snowflake.queries import (
    SNOWFLAKE_FETCH_ALL_TAGS,
    SNOWFLAKE_GET_CLUSTER_KEY,
    SNOWFLAKE_GET_CURRENT_ACCOUNT,
    SNOWFLAKE_GET_CURRENT_REGION,
    SNOWFLAKE_GET_DATABASE_COMMENTS,
    SNOWFLAKE_GET_DATABASES,
    SNOWFLAKE_GET_SCHEMA_COMMENTS,
    SNOWFLAKE_SESSION_TAG_QUERY,
)
from metadata.ingestion.source.database.snowflake.utils import (
    _current_database_schema,
    get_columns,
    get_foreign_keys,
    get_pk_constraint,
    get_schema_columns,
    get_table_comment,
    get_table_names,
    get_table_names_reflection,
    get_unique_constraints,
    get_view_definition,
    get_view_names,
    normalize_names,
)
from metadata.utils import fqn
from metadata.utils.filters import filter_by_database
from metadata.utils.logger import ingestion_logger
from metadata.utils.sqlalchemy_utils import get_all_table_comments
from metadata.utils.tag_utils import get_ometa_tag_and_classification

ischema_names["VARIANT"] = VARIANT
ischema_names["GEOGRAPHY"] = create_sqlalchemy_type("GEOGRAPHY")
ischema_names["GEOMETRY"] = create_sqlalchemy_type("GEOMETRY")

logger = ingestion_logger()


SnowflakeDialect._json_deserializer = json.loads  # pylint: disable=protected-access
SnowflakeDialect.get_table_names = get_table_names
SnowflakeDialect.get_view_names = get_view_names
SnowflakeDialect.get_all_table_comments = get_all_table_comments
SnowflakeDialect.normalize_name = normalize_names
SnowflakeDialect.get_table_comment = get_table_comment
SnowflakeDialect.get_view_definition = get_view_definition
SnowflakeDialect.get_unique_constraints = get_unique_constraints
SnowflakeDialect._get_schema_columns = (  # pylint: disable=protected-access
    get_schema_columns
)
Inspector.get_table_names = get_table_names_reflection
SnowflakeDialect._current_database_schema = (  # pylint: disable=protected-access
    _current_database_schema
)
SnowflakeDialect.get_pk_constraint = get_pk_constraint
SnowflakeDialect.get_foreign_keys = get_foreign_keys
SnowflakeDialect.get_columns = get_columns


class SnowflakeSource(CommonDbSourceService):
    """
    Implements the necessary methods to extract
    Database metadata from Snowflake Source
    """

    def __init__(self, config, metadata_config):
        super().__init__(config, metadata_config)
        self.partition_details = {}
        self.schema_desc_map = {}
        self.database_desc_map = {}

    @classmethod
    def create(cls, config_dict, metadata_config: OpenMetadataConnection):
        config: WorkflowSource = WorkflowSource.parse_obj(config_dict)
        connection: SnowflakeConnection = config.serviceConnection.__root__.config
        if not isinstance(connection, SnowflakeConnection):
            raise InvalidSourceException(
                f"Expected SnowflakeConnection, but got {connection}"
            )
        return cls(config, metadata_config)

    def set_session_query_tag(self) -> None:
        """
        Method to set query tag for current session
        """
        if self.service_connection.queryTag:
            self.engine.execute(
                SNOWFLAKE_SESSION_TAG_QUERY.format(
                    query_tag=self.service_connection.queryTag
                )
            )

    def set_partition_details(self) -> None:
        self.partition_details.clear()
        results = self.engine.execute(SNOWFLAKE_GET_CLUSTER_KEY).all()
        for row in results:
            if row.CLUSTERING_KEY:
                self.partition_details[
                    f"{row.TABLE_SCHEMA}.{row.TABLE_NAME}"
                ] = row.CLUSTERING_KEY

    def set_schema_description_map(self) -> None:
        results = self.engine.execute(SNOWFLAKE_GET_SCHEMA_COMMENTS).all()
        for row in results:
            self.schema_desc_map[(row.DATABASE_NAME, row.SCHEMA_NAME)] = row.COMMENT

    def set_database_description_map(self) -> None:
        if not self.database_desc_map:
            results = self.engine.execute(SNOWFLAKE_GET_DATABASE_COMMENTS).all()
            for row in results:
                self.database_desc_map[row.DATABASE_NAME] = row.COMMENT

    def get_schema_description(self, schema_name: str) -> Optional[str]:
        """
        Method to fetch the schema description
        """
        return self.schema_desc_map.get(
            (self.context.database.name.__root__, schema_name)
        )

    def get_database_description(self, database_name: str) -> Optional[str]:
        """
        Method to fetch the database description
        """
        return self.database_desc_map.get(database_name)

    def get_database_names(self) -> Iterable[str]:
        configured_db = self.config.serviceConnection.__root__.config.database
        if configured_db:
            self.set_inspector(configured_db)
            self.set_session_query_tag()
            self.set_partition_details()
            self.set_schema_description_map()
            self.set_database_description_map()
            yield configured_db
        else:
            results = self.connection.execute(SNOWFLAKE_GET_DATABASES)
            for res in results:
                row = list(res)
                new_database = row[1]
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
                    self.set_session_query_tag()
                    self.set_partition_details()
                    self.set_schema_description_map()
                    self.set_database_description_map()
                    yield new_database
                except Exception as exc:
                    logger.debug(traceback.format_exc())
                    logger.warning(
                        f"Error trying to connect to database {new_database}: {exc}"
                    )

    def __get_identifier_from_function(self, function_token: Function) -> List:
        identifiers = []
        for token in function_token.get_parameters():
            if isinstance(token, Function):
                # get column names from nested functions
                identifiers.extend(self.__get_identifier_from_function(token))
            elif isinstance(token, Identifier):
                identifiers.append(token.get_real_name())
        return identifiers

    def parse_column_name_from_expr(self, cluster_key_expr: str) -> Optional[List[str]]:
        try:
            parser = sqlparse.parse(cluster_key_expr)
            if not parser:
                return []
            result = []
            tokens_list = parser[0].tokens
            for token in tokens_list:
                if isinstance(token, Function):
                    result.extend(self.__get_identifier_from_function(token))
                elif isinstance(token, Identifier):
                    result.append(token.get_real_name())
            return result
        except Exception as err:
            logger.debug(traceback.format_exc())
            logger.warning(f"Failed to parse cluster key - {err}")
        return None

    def __fix_partition_column_case(
        self,
        table_name: str,
        schema_name: str,
        inspector: Inspector,
        partition_columns: Optional[List[str]],
    ) -> List[str]:
        if partition_columns:
            columns = []
            table_columns = inspector.get_columns(
                table_name=table_name, schema=schema_name
            )
            for pcolumn in partition_columns:
                for tcolumn in table_columns:
                    if tcolumn["name"].lower() == pcolumn.lower():
                        columns.append(tcolumn["name"])
                        break
            return columns
        return []

    def get_table_partition_details(
        self, table_name: str, schema_name: str, inspector: Inspector
    ) -> Tuple[bool, TablePartition]:
        cluster_key = self.partition_details.get(f"{schema_name}.{table_name}")
        if cluster_key:
            partition_columns = self.parse_column_name_from_expr(cluster_key)
            partition_details = TablePartition(
                columns=self.__fix_partition_column_case(
                    table_name, schema_name, inspector, partition_columns
                ),
                intervalType=IntervalType.COLUMN_VALUE,
            )
            return True, partition_details
        return False, None

    def yield_tag(self, schema_name: str) -> Iterable[OMetaTagAndClassification]:
        if self.source_config.includeTags:
            result = []
            try:
                result = self.connection.execute(
                    SNOWFLAKE_FETCH_ALL_TAGS.format(
                        database_name=self.context.database.name.__root__,
                        schema_name=schema_name,
                    )
                )

            except Exception as exc:
                try:
                    logger.debug(traceback.format_exc())
                    logger.warning(
                        f"Error fetching tags {exc}. Trying with quoted names"
                    )
                    result = self.connection.execute(
                        SNOWFLAKE_FETCH_ALL_TAGS.format(
                            database_name=f'"{self.context.database.name.__root__}"',
                            schema_name=f'"{self.context.database_schema.name.__root__}"',
                        )
                    )
                except Exception as inner_exc:
                    logger.debug(traceback.format_exc())
                    logger.error(f"Failed to fetch tags: {inner_exc}")

            for res in result:
                row = list(res)
                fqn_elements = [name for name in row[2:] if name]
                yield from get_ometa_tag_and_classification(
                    tag_fqn=fqn._build(  # pylint: disable=protected-access
                        self.context.database_service.name.__root__, *fqn_elements
                    ),
                    tags=[row[1]],
                    classification_name=row[0],
                    tag_description="SNOWFLAKE TAG VALUE",
                    classification_desciption="SNOWFLAKE TAG NAME",
                )

    def query_table_names_and_types(
        self, schema_name: str
    ) -> Iterable[TableNameAndType]:
        """
        Connect to the source database to get the table
        name and type. By default, use the inspector method
        to get the names and pass the Regular type.

        This is useful for sources where we need fine-grained
        logic on how to handle table types, e.g., external, foreign,...
        """
        table_list = [
            TableNameAndType(name=table_name)
            for table_name in self.inspector.get_table_names(
                schema=schema_name,
            )
        ]

        table_list.extend(
            [
                TableNameAndType(name=table_name, type_=TableType.External)
                for table_name in self.inspector.get_table_names(
                    schema=schema_name, external_tables=True
                )
            ]
        )

        if self.service_connection.includeTransientTables:
            table_list.extend(
                [
                    TableNameAndType(name=table_name, type_=TableType.Transient)
                    for table_name in self.inspector.get_table_names(
                        schema=schema_name,
                        include_transient_tables=True,
                    )
                ]
            )

        return table_list

    def _get_current_region(self) -> Optional[str]:
        try:
            res = self.engine.execute(SNOWFLAKE_GET_CURRENT_REGION).one()
            if res:
                return res.REGION
        except Exception as exc:
            logger.debug(traceback.format_exc())
            logger.debug(f"Failed to fetch current region due to: {exc}")
        return None

    def _get_current_account(self) -> Optional[str]:
        try:
            res = self.engine.execute(SNOWFLAKE_GET_CURRENT_ACCOUNT).one()
            if res:
                return res.ACCOUNT
        except Exception as exc:
            logger.debug(traceback.format_exc())
            logger.debug(f"Failed to fetch current account due to: {exc}")
        return None

    def _clean_region_name(self, region_id: Optional[str]) -> Optional[str]:
        """
        Region id can be a vanilla id like "AWS_US_WEST_2"
        and in case of multi region group it can be like "PUBLIC.AWS_US_WEST_2"
        in such cases this method will extract vanilla region id and return the
        region name from constant map SNOWFLAKE_REGION_ID_MAP

        for more info checkout this doc:
            https://docs.snowflake.com/en/sql-reference/functions/current_region
        """
        if region_id:
            clean_region_id = region_id.split(".")[-1]
            return SNOWFLAKE_REGION_ID_MAP.get(clean_region_id.lower())
        return None

    def get_source_url(
        self,
        database_name: Optional[str] = None,
        schema_name: Optional[str] = None,
        table_name: Optional[str] = None,
        table_type: Optional[TableType] = None,
    ) -> Optional[str]:
        """
        Method to get the source url for snowflake
        """
        try:
            account = self._get_current_account()
            region_id = self._get_current_region()
            region_name = self._clean_region_name(region_id)
            if account and region_name:
                tab_type = "view" if table_type == TableType.View else "table"
                url = (
                    f"https://app.snowflake.com/{region_name.lower()}"
                    f"/{account.lower()}/#/data/databases/{database_name}"
                )
                if schema_name:
                    url = f"{url}/schemas/{schema_name}"
                    if table_name:
                        url = f"{url}/{tab_type}/{table_name}"
                return url
        except Exception as exc:
            logger.debug(traceback.format_exc())
            logger.error(f"Unable to get source url: {exc}")
        return None
