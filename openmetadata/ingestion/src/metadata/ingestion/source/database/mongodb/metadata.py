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
MongoDB source methods.
"""

import traceback
from typing import Dict, List, Union

from pymongo.errors import OperationFailure

from metadata.generated.schema.entity.services.connections.database.mongoDBConnection import (
    MongoDBConnection,
)
from metadata.generated.schema.entity.services.connections.metadata.openMetadataConnection import (
    OpenMetadataConnection,
)
from metadata.generated.schema.metadataIngestion.workflow import (
    Source as WorkflowSource,
)
from metadata.ingestion.api.source import InvalidSourceException
from metadata.ingestion.source.database.common_nosql_source import (
    SAMPLE_SIZE,
    CommonNoSQLSource,
)
from metadata.utils.logger import ingestion_logger

logger = ingestion_logger()


class MongodbSource(CommonNoSQLSource):
    """
    Implements the necessary methods to extract
    Database metadata from Dynamo Source
    """

    def __init__(self, config: WorkflowSource, metadata_config: OpenMetadataConnection):
        super().__init__(config, metadata_config)
        self.mongodb = self.connection_obj

    @classmethod
    def create(cls, config_dict, metadata_config: OpenMetadataConnection):
        config: WorkflowSource = WorkflowSource.parse_obj(config_dict)
        connection: MongoDBConnection = config.serviceConnection.__root__.config
        if not isinstance(connection, MongoDBConnection):
            raise InvalidSourceException(
                f"Expected MongoDBConnection, but got {connection}"
            )
        return cls(config, metadata_config)

    def get_schema_name_list(self) -> List[str]:
        """
        Method to get list of schema names available within NoSQL db
        need to be overridden by sources
        """
        try:
            return self.mongodb.list_database_names()
        except Exception as exp:
            logger.debug(f"Failed to list database names: {exp}")
            logger.debug(traceback.format_exc())
        return []

    def get_table_name_list(self, schema_name: str) -> List[str]:
        """
        Method to get list of table names available within schema db
        need to be overridden by sources
        """
        try:
            database = self.mongodb.get_database(schema_name)
            return database.list_collection_names()
        except Exception as exp:
            logger.debug(
                f"Failed to list collection names for schema [{schema_name}]: {exp}"
            )
            logger.debug(traceback.format_exc())
        return []

    def get_table_columns_dict(
        self, schema_name: str, table_name: str
    ) -> Union[List[Dict], Dict]:
        """
        Method to get actual data available within table
        need to be overridden by sources
        """
        try:
            database = self.mongodb[schema_name]
            collection = database.get_collection(table_name)
            return list(collection.find().limit(SAMPLE_SIZE))
        except OperationFailure as opf:
            logger.debug(f"Failed to read collection [{table_name}]: {opf}")
            logger.debug(traceback.format_exc())
        return []
