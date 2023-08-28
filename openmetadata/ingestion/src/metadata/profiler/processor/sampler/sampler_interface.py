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
Interface for sampler
"""

from abc import ABC, abstractmethod
from typing import Dict, Optional

from metadata.generated.schema.entity.data.table import TableData
from metadata.profiler.api.models import ProfileSampleConfig


class SamplerInterface(ABC):
    """Sampler interface"""

    def __init__(
        self,
        client,
        table,
        profile_sample_config: Optional[ProfileSampleConfig] = None,
        partition_details: Optional[Dict] = None,
        profile_sample_query: Optional[str] = None,
    ):
        self.profile_sample = None
        self.profile_sample_type = None
        if profile_sample_config:
            self.profile_sample = profile_sample_config.profile_sample
            self.profile_sample_type = profile_sample_config.profile_sample_type
        self.client = client
        self.table = table
        self._profile_sample_query = profile_sample_query
        self.sample_limit = 100
        self._sample_rows = None
        self._partition_details = partition_details

    @abstractmethod
    def _rdn_sample_from_user_query(self):
        """Get random sample from user query"""
        raise NotImplementedError

    @abstractmethod
    def _fetch_sample_data_from_user_query(self) -> TableData:
        """Fetch sample data from user query"""
        raise NotImplementedError

    @abstractmethod
    def random_sample(self):
        """Get random sample"""
        raise NotImplementedError

    @abstractmethod
    def fetch_sample_data(self) -> TableData:
        """Fetch sample data"""
        raise NotImplementedError
