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
Base class for ingesting search index services
"""
from abc import ABC, abstractmethod
from typing import Any, Iterable, List, Optional, Set

from metadata.generated.schema.api.data.createSearchIndex import (
    CreateSearchIndexRequest,
)
from metadata.generated.schema.entity.data.searchIndex import (
    SearchIndex,
    SearchIndexSampleData,
)
from metadata.generated.schema.entity.services.connections.metadata.openMetadataConnection import (
    OpenMetadataConnection,
)
from metadata.generated.schema.entity.services.searchService import (
    SearchConnection,
    SearchService,
)
from metadata.generated.schema.metadataIngestion.searchServiceMetadataPipeline import (
    SearchServiceMetadataPipeline,
)
from metadata.generated.schema.metadataIngestion.workflow import (
    Source as WorkflowSource,
)
from metadata.ingestion.api.source import Source
from metadata.ingestion.api.topology_runner import TopologyRunnerMixin
from metadata.ingestion.models.delete_entity import (
    DeleteEntity,
    delete_entity_from_source,
)
from metadata.ingestion.models.search_index_data import OMetaIndexSampleData
from metadata.ingestion.models.topology import (
    NodeStage,
    ServiceTopology,
    TopologyNode,
    create_source_context,
)
from metadata.ingestion.ometa.ometa_api import OpenMetadata
from metadata.ingestion.source.connections import get_connection, get_test_connection_fn
from metadata.utils import fqn
from metadata.utils.filters import filter_by_search_index
from metadata.utils.logger import ingestion_logger

logger = ingestion_logger()


class SearchServiceTopology(ServiceTopology):
    """
    Defines the hierarchy in Search Services.

    We could have a topology validator. We can only consume
    data that has been produced by any parent node.
    """

    root = TopologyNode(
        producer="get_services",
        stages=[
            NodeStage(
                type_=SearchService,
                context="search_service",
                processor="yield_create_request_search_service",
                overwrite=False,
                must_return=True,
            ),
        ],
        children=["search_index"],
        post_process=["mark_search_indexes_as_deleted"],
    )
    search_index = TopologyNode(
        producer="get_search_index",
        stages=[
            NodeStage(
                type_=SearchIndex,
                context="search_index",
                processor="yield_search_index",
                consumer=["search_service"],
            ),
            NodeStage(
                type_=OMetaIndexSampleData,
                context="search_index_sample_data",
                processor="yield_search_index_sample_data",
                consumer=["search_service"],
                ack_sink=False,
                nullable=True,
            ),
        ],
    )


class SearchServiceSource(TopologyRunnerMixin, Source, ABC):
    """
    Base class for Search Services.
    It implements the topology and context.
    """

    source_config: SearchServiceMetadataPipeline
    config: WorkflowSource
    # Big union of types we want to fetch dynamically
    service_connection: SearchConnection.__fields__["config"].type_

    topology = SearchServiceTopology()
    context = create_source_context(topology)
    index_source_state: Set = set()

    def __init__(
        self,
        config: WorkflowSource,
        metadata_config: OpenMetadataConnection,
    ):
        super().__init__()
        self.config = config
        self.metadata_config = metadata_config
        self.metadata = OpenMetadata(metadata_config)
        self.source_config: SearchServiceMetadataPipeline = (
            self.config.sourceConfig.config
        )
        self.service_connection = self.config.serviceConnection.__root__.config
        self.connection = get_connection(self.service_connection)

        # Flag the connection for the test connection
        self.connection_obj = self.connection
        self.test_connection()

    @abstractmethod
    def yield_search_index(
        self, search_index_details: Any
    ) -> Iterable[CreateSearchIndexRequest]:
        """
        Method to Get Search Index Entity
        """

    def yield_search_index_sample_data(
        self, search_index_details: Any
    ) -> Iterable[SearchIndexSampleData]:
        """
        Method to Get Sample Data of Search Index Entity
        """

    @abstractmethod
    def get_search_index_list(self) -> Optional[List[Any]]:
        """
        Get List of all search index
        """

    @abstractmethod
    def get_search_index_name(self, search_index_details: Any) -> str:
        """
        Get Search Index Name
        """

    def get_search_index(self) -> Any:
        for index_details in self.get_search_index_list():
            search_index_name = self.get_search_index_name(index_details)
            if filter_by_search_index(
                self.source_config.searchIndexFilterPattern,
                search_index_name,
            ):
                self.status.filter(
                    search_index_name,
                    "Search Index Filtered Out",
                )
                continue
            yield index_details

    def yield_create_request_search_service(self, config: WorkflowSource):
        yield self.metadata.get_create_service_from_source(
            entity=SearchService, config=config
        )

    def get_services(self) -> Iterable[WorkflowSource]:
        yield self.config

    def prepare(self):
        """
        Nothing to prepare by default
        """

    def test_connection(self) -> None:
        test_connection_fn = get_test_connection_fn(self.service_connection)
        test_connection_fn(self.metadata, self.connection_obj, self.service_connection)

    def mark_search_indexes_as_deleted(self) -> Iterable[DeleteEntity]:
        """
        Method to mark the search index as deleted
        """
        if self.source_config.markDeletedSearchIndexes:
            yield from delete_entity_from_source(
                metadata=self.metadata,
                entity_type=SearchIndex,
                entity_source_state=self.index_source_state,
                mark_deleted_entity=self.source_config.markDeletedSearchIndexes,
                params={
                    "service": self.context.search_service.fullyQualifiedName.__root__
                },
            )

    def register_record(self, search_index_request: CreateSearchIndexRequest) -> None:
        """
        Mark the search index record as scanned and update the index_source_state
        """
        index_fqn = fqn.build(
            self.metadata,
            entity_type=SearchIndex,
            service_name=search_index_request.service.__root__,
            search_index_name=search_index_request.name.__root__,
        )

        self.index_source_state.add(index_fqn)
        self.status.scanned(search_index_request.name.__root__)

    def close(self):
        """
        Nothing to close by default
        """
