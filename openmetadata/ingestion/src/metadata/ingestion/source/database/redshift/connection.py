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
Source connection handler
"""
from typing import Optional

from sqlalchemy.engine import Engine

from metadata.generated.schema.entity.automations.workflow import (
    Workflow as AutomationWorkflow,
)
from metadata.generated.schema.entity.services.connections.database.redshiftConnection import (
    RedshiftConnection,
    SslMode,
)
from metadata.ingestion.connections.builders import (
    create_generic_db_connection,
    get_connection_args_common,
    get_connection_url_common,
    init_empty_connection_arguments,
)
from metadata.ingestion.connections.test_connections import test_connection_db_common
from metadata.ingestion.ometa.ometa_api import OpenMetadata
from metadata.ingestion.source.database.redshift.queries import (
    REDSHIFT_GET_DATABASE_NAMES,
    REDSHIFT_TEST_GET_QUERIES,
    REDSHIFT_TEST_PARTITION_DETAILS,
)


def get_connection(connection: RedshiftConnection) -> Engine:
    """
    Create connection
    """
    if connection.sslMode:
        if not connection.connectionArguments:
            connection.connectionArguments = init_empty_connection_arguments()
        connection.connectionArguments.__root__["sslmode"] = connection.sslMode.value
        if connection.sslMode in (SslMode.verify_ca, SslMode.verify_full):
            connection.connectionArguments.__root__[
                "sslrootcert"
            ] = connection.sslConfig.__root__.certificatePath
    return create_generic_db_connection(
        connection=connection,
        get_connection_url_fn=get_connection_url_common,
        get_connection_args_fn=get_connection_args_common,
    )


def test_connection(
    metadata: OpenMetadata,
    engine: Engine,
    service_connection: RedshiftConnection,
    automation_workflow: Optional[AutomationWorkflow] = None,
) -> None:
    """
    Test connection. This can be executed either as part
    of a metadata workflow or during an Automation Workflow
    """
    queries = {
        "GetQueries": REDSHIFT_TEST_GET_QUERIES,
        "GetDatabases": REDSHIFT_GET_DATABASE_NAMES,
        "GetPartitionTableDetails": REDSHIFT_TEST_PARTITION_DETAILS,
    }
    test_connection_db_common(
        metadata=metadata,
        engine=engine,
        service_connection=service_connection,
        automation_workflow=automation_workflow,
        queries=queries,
    )
