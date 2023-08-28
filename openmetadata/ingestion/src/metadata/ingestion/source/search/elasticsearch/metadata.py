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
Elasticsearch source to extract metadata
"""
from typing import Any, Iterable, Optional

from elasticsearch import Elasticsearch

from metadata.generated.schema.api.data.createSearchIndex import (
    CreateSearchIndexRequest,
)
from metadata.generated.schema.entity.data.searchIndex import SearchIndexSampleData
from metadata.generated.schema.entity.services.connections.metadata.openMetadataConnection import (
    OpenMetadataConnection,
)
from metadata.generated.schema.entity.services.connections.search.elasticSearchConnection import (
    ElasticsearchConnection,
)
from metadata.generated.schema.metadataIngestion.workflow import (
    Source as WorkflowSource,
)
from metadata.ingestion.api.source import InvalidSourceException, Source
from metadata.ingestion.models.search_index_data import OMetaIndexSampleData
from metadata.ingestion.source.search.elasticsearch.parser import parse_es_index_mapping
from metadata.ingestion.source.search.search_service import SearchServiceSource
from metadata.utils.logger import ingestion_logger

logger = ingestion_logger()


WILDCARD_SEARCH = "*"


class ElasticsearchSource(SearchServiceSource):
    """
    Implements the necessary methods ot extract
    Search Index metadata from Elastic Search
    """

    def __init__(self, config: Source, metadata_config: OpenMetadataConnection):
        super().__init__(config, metadata_config)
        self.client: Elasticsearch = self.connection

    @classmethod
    def create(cls, config_dict, metadata_config: OpenMetadataConnection):
        config: WorkflowSource = WorkflowSource.parse_obj(config_dict)
        connection: ElasticsearchConnection = config.serviceConnection.__root__.config
        if not isinstance(connection, ElasticsearchConnection):
            raise InvalidSourceException(
                f"Expected ElasticsearchConnection, but got {connection}"
            )
        return cls(config, metadata_config)

    def get_search_index_list(self) -> Iterable[dict]:
        """
        Get List of all search index
        """
        index_list = self.client.indices.get_alias() or {}
        for index in index_list.keys():
            yield self.client.indices.get(index)

    def get_search_index_name(self, search_index_details: dict) -> Optional[str]:
        """
        Get Search Index Name
        """
        if search_index_details and len(search_index_details) == 1:
            return list(search_index_details.keys())[0]

        return None

    def yield_search_index(
        self, search_index_details: Any
    ) -> Iterable[CreateSearchIndexRequest]:
        """
        Method to Get Search Index Entity
        """
        index_name = self.get_search_index_name(search_index_details)
        if index_name:
            yield CreateSearchIndexRequest(
                name=index_name,
                displayName=index_name,
                searchIndexSettings=search_index_details.get(index_name, {}).get(
                    "settings", {}
                ),
                service=self.context.search_service.fullyQualifiedName.__root__,
                fields=parse_es_index_mapping(
                    search_index_details.get(index_name, {}).get("mappings")
                ),
            )

    def yield_search_index_sample_data(
        self, search_index_details: Any
    ) -> Iterable[OMetaIndexSampleData]:
        """
        Method to Get Sample Data of Search Index Entity
        """
        if self.source_config.includeSampleData and self.context.search_index:

            sample_data = self.client.search(
                index=self.context.search_index.name.__root__,
                q=WILDCARD_SEARCH,
                size=self.source_config.sampleSize,
                request_timeout=self.service_connection.connectionTimeoutSecs,
            )

            yield OMetaIndexSampleData(
                entity=self.context.search_index,
                data=SearchIndexSampleData(
                    messages=[
                        str(message)
                        for message in sample_data.get("hits", {}).get("hits", [])
                    ]
                ),
            )
