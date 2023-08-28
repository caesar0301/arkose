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
Abstract Processor definition to build a Workflow
"""
from abc import ABCMeta, abstractmethod
from dataclasses import dataclass
from typing import Any, Generic, List, Optional

from pydantic import Field

from metadata.generated.schema.entity.services.connections.metadata.openMetadataConnection import (
    OpenMetadataConnection,
)
from metadata.ingestion.api.closeable import Closeable
from metadata.ingestion.api.common import Entity
from metadata.ingestion.api.status import Status
from metadata.utils.logger import ingestion_logger

logger = ingestion_logger()


class ProcessorStatus(Status):
    records: List[str] = Field(default_factory=list)

    def processed(self, record: Any):
        self.records.append(record)

    def warning(self, info: Any) -> None:
        self.warnings.append(info)


class ProfilerProcessorStatus(Status):
    entity: Optional[str] = None

    def scanned(self, record: Any) -> None:
        self.records.append(record)

    def failed_profiler(self, error: str, stack_trace: Optional[str] = None) -> None:
        self.failed(self.entity if self.entity else "", error, stack_trace)


@dataclass
class Processor(Closeable, Generic[Entity], metaclass=ABCMeta):
    """
    Processor class
    """

    status: ProcessorStatus

    def __init__(self):
        self.status = ProcessorStatus()

    @classmethod
    @abstractmethod
    def create(
        cls, config_dict: dict, metadata_config: OpenMetadataConnection, **kwargs
    ) -> "Processor":
        pass

    @abstractmethod
    def process(self, *args, **kwargs) -> Entity:
        pass

    def get_status(self) -> ProcessorStatus:
        return self.status

    @abstractmethod
    def close(self) -> None:
        pass
