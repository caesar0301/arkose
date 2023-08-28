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
Source connection handler for S3 object store. For this to work, it requires the following S3 permissions for all
the buckets which require ingestion: s3:ListBucket, s3:GetObject and s3:GetBucketLocation
The cloudwatch client is used to fetch the total size in bytes for a bucket, and the total nr of files. This requires
the cloudwatch:GetMetricData permissions
"""
from dataclasses import dataclass
from functools import partial
from typing import Optional

from botocore.client import BaseClient

from metadata.clients.aws_client import AWSClient
from metadata.generated.schema.entity.automations.workflow import (
    Workflow as AutomationWorkflow,
)
from metadata.generated.schema.entity.services.connections.storage.s3Connection import (
    S3Connection,
)
from metadata.ingestion.connections.test_connections import test_connection_steps
from metadata.ingestion.ometa.ometa_api import OpenMetadata


@dataclass
class S3ObjectStoreClient:
    s3_client: BaseClient
    cloudwatch_client: BaseClient


def get_connection(connection: S3Connection) -> S3ObjectStoreClient:
    """
    Returns 2 clients - the s3 client and the cloudwatch client needed for total nr of objects and total size
    """
    aws_client = AWSClient(connection.awsConfig)
    return S3ObjectStoreClient(
        s3_client=aws_client.get_client(service_name="s3"),
        cloudwatch_client=aws_client.get_client(service_name="cloudwatch"),
    )


def test_connection(
    metadata: OpenMetadata,
    client: S3ObjectStoreClient,
    service_connection: S3Connection,
    automation_workflow: Optional[AutomationWorkflow] = None,
) -> None:
    """
    Test connection. This can be executed either as part
    of a metadata workflow or during an Automation Workflow
    """

    test_fn = {
        "ListBuckets": client.s3_client.list_buckets,
        "GetMetrics": partial(
            client.cloudwatch_client.list_metrics, Namespace="AWS/S3"
        ),
    }

    test_connection_steps(
        metadata=metadata,
        test_fn=test_fn,
        service_type=service_connection.type.value,
        automation_workflow=automation_workflow,
    )
