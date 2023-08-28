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
SAP Hana source module
"""
from typing import Iterable

from sqlalchemy import inspect

from metadata.generated.schema.entity.services.connections.database.sapHanaConnection import (
    SapHanaConnection,
)
from metadata.generated.schema.entity.services.connections.metadata.openMetadataConnection import (
    OpenMetadataConnection,
)
from metadata.generated.schema.metadataIngestion.workflow import (
    Source as WorkflowSource,
)
from metadata.ingestion.api.source import InvalidSourceException
from metadata.ingestion.source.database.common_db_source import CommonDbSourceService
from metadata.utils.logger import ingestion_logger

logger = ingestion_logger()


class SaphanaSource(CommonDbSourceService):
    """
    Implements the necessary methods to extract
    Database metadata from Mysql Source
    """

    @classmethod
    def create(cls, config_dict, metadata_config: OpenMetadataConnection):
        config: WorkflowSource = WorkflowSource.parse_obj(config_dict)
        connection: SapHanaConnection = config.serviceConnection.__root__.config
        if not isinstance(connection, SapHanaConnection):
            raise InvalidSourceException(
                f"Expected SapHanaConnection, but got {connection}"
            )
        return cls(config, metadata_config)

    def get_database_names(self) -> Iterable[str]:
        """
        Check if the db is configured, or query the name
        """
        self.inspector = inspect(self.engine)

        if getattr(self.service_connection.connection, "database"):
            yield self.service_connection.connection.database

        else:
            try:
                yield self.connection.execute(
                    "SELECT DATABASE_NAME FROM M_DATABASE"
                ).fetchone()[0]
            except Exception as err:
                raise RuntimeError(
                    f"Error retrieving database name from the source - [{err}]."
                    " A way through this error is by specifying the `database` in the service connection."
                )

    def get_raw_database_schema_names(self) -> Iterable[str]:
        if self.service_connection.connection.__dict__.get("databaseSchema"):
            yield self.service_connection.connection.databaseSchema
        else:
            for schema_name in self.inspector.get_schema_names():
                yield schema_name
