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
Client to interact with Spline consumer apis
"""
import traceback
from typing import List

from metadata.generated.schema.entity.services.connections.pipeline.splineConnection import (
    SplineConnection,
)
from metadata.ingestion.ometa.client import REST, ClientConfig
from metadata.ingestion.source.pipeline.spline.models import (
    ExecutionDetail,
    ExecutionEvents,
)
from metadata.utils.constants import AUTHORIZATION_HEADER, NO_ACCESS_TOKEN
from metadata.utils.helpers import clean_uri
from metadata.utils.logger import ingestion_logger

logger = ingestion_logger()


class SplineClient:
    """
    Wrapper on top of Spline REST API
    """

    def __init__(self, config: SplineConnection):
        self.config = config
        client_config: ClientConfig = ClientConfig(
            base_url=clean_uri(self.config.hostPort),
            api_version="consumer",
            auth_header=AUTHORIZATION_HEADER,
            auth_token=lambda: (NO_ACCESS_TOKEN, 0),
        )
        self.client = REST(client_config)

    def _paginate_pipelines(self, pipelines: ExecutionEvents):
        while pipelines.pageNum * pipelines.pageSize < pipelines.totalCount:
            try:
                response = self.client.get(
                    f"/execution-events?pageNum={pipelines.pageNum+1}"
                )
                pipelines = ExecutionEvents(**response)
                yield pipelines
            except Exception as exe:
                pipelines.pageNum += 1
                logger.debug(traceback.format_exc())
                logger.error(f"failed to fetch pipeline list due to: {exe}")

    def get_pipelines(self) -> List[dict]:
        """
        Method returns the executions events as pipelines
        """
        try:
            response = self.client.get("/execution-events")
            if response:
                pipelines = ExecutionEvents(**response)
                yield pipelines
                yield from self._paginate_pipelines(pipelines)
        except Exception as exe:
            logger.debug(traceback.format_exc())
            logger.error(f"failed to fetch pipeline list due to: {exe}")

    def get_pipelines_test_connection(self) -> List[dict]:
        """
        Method returns the executions events as pipelines
        """
        response = self.client.get("/execution-events")
        return ExecutionEvents(**response)

    def get_lineage_details(self, pipeline_id: str) -> List[dict]:
        """
        Method returns the executions events as pipelines
        """
        try:
            response = self.client.get(f"/lineage-detailed?execId={pipeline_id}")
            if response:
                return ExecutionDetail(**response)
        except Exception as exe:
            logger.debug(traceback.format_exc())
            logger.error(f"failed to fetch pipeline list due to: {exe}")

        return None
