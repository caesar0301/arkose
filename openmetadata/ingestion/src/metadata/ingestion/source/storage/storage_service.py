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
Base class for ingesting Object Storage services
"""
from abc import ABC, abstractmethod
from typing import Any, Iterable, List, Optional

from pandas import DataFrame

from metadata.generated.schema.api.data.createContainer import CreateContainerRequest
from metadata.generated.schema.entity.data.container import Container
from metadata.generated.schema.entity.services.connections.metadata.openMetadataConnection import (
    OpenMetadataConnection,
)
from metadata.generated.schema.entity.services.storageService import (
    StorageConnection,
    StorageService,
)
from metadata.generated.schema.metadataIngestion.storage.containerMetadataConfig import (
    MetadataEntry,
)
from metadata.generated.schema.metadataIngestion.storageServiceMetadataPipeline import (
    StorageServiceMetadataPipeline,
)
from metadata.generated.schema.metadataIngestion.workflow import (
    Source as WorkflowSource,
)
from metadata.ingestion.api.source import Source, SourceStatus
from metadata.ingestion.api.topology_runner import TopologyRunnerMixin
from metadata.ingestion.models.topology import (
    NodeStage,
    ServiceTopology,
    TopologyNode,
    create_source_context,
)
from metadata.ingestion.ometa.ometa_api import OpenMetadata
from metadata.ingestion.source.connections import get_connection, get_test_connection_fn
from metadata.ingestion.source.database.datalake.metadata import DatalakeSource
from metadata.ingestion.source.database.glue.models import Column
from metadata.readers.dataframe.models import DatalakeTableSchemaWrapper
from metadata.readers.models import ConfigSource
from metadata.utils.datalake.datalake_utils import fetch_dataframe
from metadata.utils.logger import ingestion_logger

logger = ingestion_logger()

OPENMETADATA_TEMPLATE_FILE_NAME = "openmetadata.json"
KEY_SEPARATOR = "/"


class StorageServiceTopology(ServiceTopology):

    root = TopologyNode(
        producer="get_services",
        stages=[
            NodeStage(
                type_=StorageService,
                context="objectstore_service",
                processor="yield_create_request_objectstore_service",
                overwrite=False,
                must_return=True,
            ),
        ],
        children=["container"],
    )

    container = TopologyNode(
        producer="get_containers",
        stages=[
            NodeStage(
                type_=Container,
                context="container",
                processor="yield_create_container_requests",
                consumer=["objectstore_service"],
                nullable=True,
            )
        ],
    )


class StorageServiceSource(TopologyRunnerMixin, Source, ABC):
    """
    Base class for Object Store Services.
    It implements the topology and context.
    """

    source_config: StorageServiceMetadataPipeline
    config: WorkflowSource
    metadata: OpenMetadata
    # Big union of types we want to fetch dynamically
    service_connection: StorageConnection.__fields__["config"].type_

    topology = StorageServiceTopology()
    context = create_source_context(topology)

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
        self.source_config: StorageServiceMetadataPipeline = (
            self.config.sourceConfig.config
        )
        self.connection = get_connection(self.service_connection)

        # Flag the connection for the test connection
        self.connection_obj = self.connection
        self.test_connection()

    @abstractmethod
    def get_containers(self) -> Iterable[Any]:
        """
        Retrieve all containers for the service
        """

    @abstractmethod
    def yield_create_container_requests(
        self, container_details: Any
    ) -> Iterable[CreateContainerRequest]:
        """Generate the create container requests based on the received details"""

    def get_status(self) -> SourceStatus:
        return self.status

    def close(self):
        """
        By default, nothing needs to be closed
        """

    def get_services(self) -> Iterable[WorkflowSource]:
        yield self.config

    def prepare(self):
        """
        By default, nothing needs to be taken care of when loading the source
        """

    def test_connection(self) -> None:
        test_connection_fn = get_test_connection_fn(self.service_connection)
        test_connection_fn(self.metadata, self.connection_obj, self.service_connection)

    def yield_create_request_objectstore_service(self, config: WorkflowSource):
        yield self.metadata.get_create_service_from_source(
            entity=StorageService, config=config
        )

    @staticmethod
    def _get_sample_file_prefix(metadata_entry: MetadataEntry) -> Optional[str]:
        """
        Return a prefix if we have structure data to read
        """
        result = f"{metadata_entry.dataPath.strip(KEY_SEPARATOR)}"
        if not metadata_entry.structureFormat:
            logger.warning(f"Ignoring un-structured metadata entry {result}")
            return None
        return result

    @staticmethod
    def extract_column_definitions(
        bucket_name: str,
        sample_key: str,
        config_source: ConfigSource,
        client: Any,
    ) -> List[Column]:
        """
        Extract Column related metadata from s3
        """
        data_structure_details = fetch_dataframe(
            config_source=config_source,
            client=client,
            file_fqn=DatalakeTableSchemaWrapper(
                key=sample_key, bucket_name=bucket_name
            ),
        )
        columns = []
        if isinstance(data_structure_details, DataFrame):
            columns = DatalakeSource.get_columns(data_structure_details)
        if isinstance(data_structure_details, list) and data_structure_details:
            columns = DatalakeSource.get_columns(data_structure_details[0])
        return columns

    def _get_columns(
        self,
        container_name: str,
        sample_key: str,
        metadata_entry: MetadataEntry,
        config_source: ConfigSource,
        client: Any,
    ) -> Optional[List[Column]]:
        """
        Get the columns from the file and partition information
        """
        extracted_cols = self.extract_column_definitions(
            container_name, sample_key, config_source, client
        )
        return (metadata_entry.partitionColumns or []) + (extracted_cols or [])
