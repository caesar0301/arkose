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

from metadata.generated.schema.entity.automations.workflow import (
    Workflow as AutomationWorkflow,
)
from metadata.generated.schema.entity.services.connections.messaging.redpandaConnection import (
    RedpandaConnection,
)
from metadata.ingestion.ometa.ometa_api import OpenMetadata
from metadata.ingestion.source.messaging.kafka.connection import KafkaClient
from metadata.ingestion.source.messaging.kafka.connection import (
    get_connection as get_kafka_connection,
)
from metadata.ingestion.source.messaging.kafka.connection import (
    test_connection as test_kafka_connection,
)
from metadata.utils.logger import ingestion_logger

logger = ingestion_logger()


def get_connection(connection: RedpandaConnection) -> KafkaClient:
    """
    Create connection
    """
    return get_kafka_connection(connection)


def test_connection(
    metadata: OpenMetadata,
    client: KafkaClient,
    service_connection: RedpandaConnection,
    automation_workflow: Optional[AutomationWorkflow] = None,
) -> None:
    """
    Test connection. This can be executed either as part
    of a metadata workflow or during an Automation Workflow
    """

    test_kafka_connection(
        metadata=metadata,
        client=client,
        service_connection=service_connection,
        automation_workflow=automation_workflow,
    )
