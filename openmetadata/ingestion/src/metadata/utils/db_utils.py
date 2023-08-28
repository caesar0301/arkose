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
Helpers module for db sources
"""

import traceback

from metadata.generated.schema.entity.data.table import Table
from metadata.ingestion.lineage.models import ConnectionTypeDialectMapper
from metadata.ingestion.lineage.parser import LINEAGE_PARSING_TIMEOUT, LineageParser
from metadata.ingestion.lineage.sql_lineage import (
    get_lineage_by_query,
    get_lineage_via_table_entity,
)
from metadata.ingestion.ometa.ometa_api import OpenMetadata
from metadata.ingestion.source.models import TableView
from metadata.utils import fqn
from metadata.utils.logger import utils_logger

logger = utils_logger()


def get_host_from_host_port(uri: str) -> str:
    """
    if uri is like "localhost:9000"
    then return the host "localhost"
    """
    return uri.split(":")[0]


def get_view_lineage(
    view: TableView,
    metadata: OpenMetadata,
    service_name: str,
    connection_type: str,
    timeout_seconds: int = LINEAGE_PARSING_TIMEOUT,
):
    """
    Method to generate view lineage
    """
    table_name = view.table_name
    schema_name = view.schema_name
    db_name = view.db_name
    view_definition = view.view_definition
    table_fqn = fqn.build(
        metadata,
        entity_type=Table,
        service_name=service_name,
        database_name=db_name,
        schema_name=schema_name,
        table_name=table_name,
    )
    table_entity = metadata.get_by_name(
        entity=Table,
        fqn=table_fqn,
    )

    try:
        connection_type = str(connection_type)
        dialect = ConnectionTypeDialectMapper.dialect_of(connection_type)
        lineage_parser = LineageParser(
            view_definition, dialect, timeout_seconds=timeout_seconds
        )
        if lineage_parser.source_tables and lineage_parser.target_tables:
            yield from get_lineage_by_query(
                metadata,
                query=view_definition,
                service_name=service_name,
                database_name=db_name,
                schema_name=schema_name,
                dialect=dialect,
                timeout_seconds=timeout_seconds,
            ) or []

        else:
            yield from get_lineage_via_table_entity(
                metadata,
                table_entity=table_entity,
                service_name=service_name,
                database_name=db_name,
                schema_name=schema_name,
                query=view_definition,
                dialect=dialect,
                timeout_seconds=timeout_seconds,
            ) or []
    except Exception as exc:
        logger.debug(traceback.format_exc())
        logger.warning(
            f"Could not parse query [{view_definition}] ingesting lineage failed: {exc}"
        )
