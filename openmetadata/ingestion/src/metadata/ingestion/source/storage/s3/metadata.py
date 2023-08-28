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
"""S3 object store extraction metadata"""
import json
import secrets
import traceback
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, Iterable, List, Optional

from pydantic import ValidationError

from metadata.generated.schema.api.data.createContainer import CreateContainerRequest
from metadata.generated.schema.entity.data import container
from metadata.generated.schema.entity.data.container import (
    Container,
    ContainerDataModel,
)
from metadata.generated.schema.entity.services.connections.database.datalake.s3Config import (
    S3Config,
)
from metadata.generated.schema.entity.services.connections.metadata.openMetadataConnection import (
    OpenMetadataConnection,
)
from metadata.generated.schema.entity.services.connections.storage.s3Connection import (
    S3Connection,
)
from metadata.generated.schema.metadataIngestion.storage.containerMetadataConfig import (
    MetadataEntry,
    StorageContainerConfig,
)
from metadata.generated.schema.metadataIngestion.workflow import (
    Source as WorkflowSource,
)
from metadata.generated.schema.type.entityReference import EntityReference
from metadata.ingestion.api.source import InvalidSourceException
from metadata.ingestion.source.storage.s3.models import (
    S3BucketResponse,
    S3ContainerDetails,
)
from metadata.ingestion.source.storage.storage_service import (
    KEY_SEPARATOR,
    OPENMETADATA_TEMPLATE_FILE_NAME,
    StorageServiceSource,
)
from metadata.utils.filters import filter_by_container
from metadata.utils.logger import ingestion_logger

logger = ingestion_logger()

S3_CLIENT_ROOT_RESPONSE = "Contents"


class S3Metric(Enum):
    NUMBER_OF_OBJECTS = "NumberOfObjects"
    BUCKET_SIZE_BYTES = "BucketSizeBytes"


class S3Source(StorageServiceSource):
    """
    Source implementation to ingest S3 buckets data.
    """

    def __init__(self, config: WorkflowSource, metadata_config: OpenMetadataConnection):
        super().__init__(config, metadata_config)
        self.s3_client = self.connection.s3_client
        self.cloudwatch_client = self.connection.cloudwatch_client

        self._bucket_cache: Dict[str, Container] = {}

    @classmethod
    def create(cls, config_dict, metadata_config: OpenMetadataConnection):
        config: WorkflowSource = WorkflowSource.parse_obj(config_dict)
        connection: S3Connection = config.serviceConnection.__root__.config
        if not isinstance(connection, S3Connection):
            raise InvalidSourceException(
                f"Expected S3StoreConnection, but got {connection}"
            )
        return cls(config, metadata_config)

    def get_containers(self) -> Iterable[S3ContainerDetails]:
        bucket_results = self.fetch_buckets()

        for bucket_response in bucket_results:
            try:

                # We always try to generate the parent container (the bucket)
                yield self._generate_unstructured_container(
                    bucket_response=bucket_response
                )
                self._bucket_cache[bucket_response.name] = self.context.container

                metadata_config = self._load_metadata_file(
                    bucket_name=bucket_response.name
                )
                if metadata_config:
                    for metadata_entry in metadata_config.entries:
                        logger.info(
                            f"Extracting metadata from path {metadata_entry.dataPath.strip(KEY_SEPARATOR)} "
                            f"and generating structured container"
                        )
                        structured_container: Optional[
                            S3ContainerDetails
                        ] = self._generate_container_details(
                            bucket_response=bucket_response,
                            metadata_entry=metadata_entry,
                            parent=EntityReference(
                                id=self._bucket_cache[bucket_response.name].id.__root__,
                                type="container",
                            ),
                        )
                        if structured_container:
                            yield structured_container

            except ValidationError as err:
                error = f"Validation error while creating Container from bucket details - {err}"
                logger.debug(traceback.format_exc())
                logger.warning(error)
                self.status.failed(bucket_response.name, error, traceback.format_exc())
            except Exception as err:
                error = (
                    f"Wild error while creating Container from bucket details - {err}"
                )
                logger.debug(traceback.format_exc())
                logger.warning(error)
                self.status.failed(bucket_response.name, error, traceback.format_exc())

    def yield_create_container_requests(
        self, container_details: S3ContainerDetails
    ) -> Iterable[CreateContainerRequest]:
        yield CreateContainerRequest(
            name=container_details.name,
            prefix=container_details.prefix,
            numberOfObjects=container_details.number_of_objects,
            size=container_details.size,
            dataModel=container_details.data_model,
            service=self.context.objectstore_service.fullyQualifiedName,
            parent=container_details.parent,
            sourceUrl=container_details.sourceUrl,
            fileFormats=container_details.file_formats,
        )

    def _generate_container_details(
        self,
        bucket_response: S3BucketResponse,
        metadata_entry: MetadataEntry,
        parent: Optional[EntityReference] = None,
    ) -> Optional[S3ContainerDetails]:
        bucket_name = bucket_response.name
        sample_key = self._get_sample_file_path(
            bucket_name=bucket_name, metadata_entry=metadata_entry
        )
        # if we have a sample file to fetch a schema from
        if sample_key:

            columns = self._get_columns(
                container_name=bucket_name,
                sample_key=sample_key,
                metadata_entry=metadata_entry,
                config_source=S3Config(
                    securityConfig=self.service_connection.awsConfig
                ),
                client=self.s3_client,
            )
            if columns:
                return S3ContainerDetails(
                    name=metadata_entry.dataPath.strip(KEY_SEPARATOR),
                    prefix=f"{KEY_SEPARATOR}{metadata_entry.dataPath.strip(KEY_SEPARATOR)}",
                    creation_date=bucket_response.creation_date.isoformat(),
                    number_of_objects=self._fetch_metric(
                        bucket_name=bucket_name, metric=S3Metric.NUMBER_OF_OBJECTS
                    ),
                    size=self._fetch_metric(
                        bucket_name=bucket_name, metric=S3Metric.BUCKET_SIZE_BYTES
                    ),
                    file_formats=[container.FileFormat(metadata_entry.structureFormat)],
                    data_model=ContainerDataModel(
                        isPartitioned=metadata_entry.isPartitioned, columns=columns
                    ),
                    parent=parent,
                    sourceUrl=self._get_object_source_url(
                        bucket_name=bucket_name,
                        prefix=metadata_entry.dataPath.strip(KEY_SEPARATOR),
                    ),
                )
        return None

    def fetch_buckets(self) -> List[S3BucketResponse]:
        results: List[S3BucketResponse] = []
        try:
            # No pagination required, as there is a hard 1000 limit on nr of buckets per aws account
            for bucket in self.s3_client.list_buckets().get("Buckets") or []:
                if filter_by_container(
                    self.source_config.containerFilterPattern,
                    container_name=bucket["Name"],
                ):
                    self.status.filter(bucket["Name"], "Bucket Filtered Out")
                else:
                    results.append(S3BucketResponse.parse_obj(bucket))
        except Exception as err:
            logger.debug(traceback.format_exc())
            logger.error(f"Failed to fetch buckets list - {err}")
        return results

    def _fetch_metric(self, bucket_name: str, metric: S3Metric) -> float:
        try:
            raw_result = self.cloudwatch_client.get_metric_data(
                MetricDataQueries=[
                    {
                        "Id": "total_nr_of_object_request",
                        "MetricStat": {
                            "Metric": {
                                "Namespace": "AWS/S3",
                                "MetricName": metric.value,
                                "Dimensions": [
                                    {"Name": "BucketName", "Value": bucket_name},
                                    {
                                        "Name": "StorageType",
                                        # StandardStorage-only support for BucketSizeBytes for now
                                        "Value": "StandardStorage"
                                        if metric == S3Metric.BUCKET_SIZE_BYTES
                                        else "AllStorageTypes",
                                    },
                                ],
                            },
                            "Period": 60,
                            "Stat": "Average",
                            "Unit": "Bytes"
                            if metric == S3Metric.BUCKET_SIZE_BYTES
                            else "Count",
                        },
                    },
                ],
                StartTime=datetime.now() - timedelta(days=2),
                # metrics generated daily, ensure there is at least 1 entry
                EndTime=datetime.now(),
                ScanBy="TimestampDescending",
            )
            if raw_result["MetricDataResults"]:
                first_metric = raw_result["MetricDataResults"][0]
                if first_metric["StatusCode"] == "Complete" and first_metric["Values"]:
                    return int(first_metric["Values"][0])
        except Exception:
            logger.debug(traceback.format_exc())
            logger.warning(
                f"Failed fetching metric {metric.value} for bucket {bucket_name}, returning 0"
            )
        return 0

    def _load_metadata_file(self, bucket_name: str) -> Optional[StorageContainerConfig]:
        """
        Load the metadata template file from the root of the bucket, if it exists
        """
        if self._is_metadata_file_present(bucket_name=bucket_name):
            try:
                logger.info(
                    f"Found metadata template file at - s3://{bucket_name}/{OPENMETADATA_TEMPLATE_FILE_NAME}"
                )
                response_object = self.s3_client.get_object(
                    Bucket=bucket_name, Key=OPENMETADATA_TEMPLATE_FILE_NAME
                )
                content = json.load(response_object["Body"])
                metadata_config = StorageContainerConfig.parse_obj(content)
                return metadata_config
            except Exception as exc:
                logger.debug(traceback.format_exc())
                logger.warning(
                    f"Failed loading metadata file s3://{bucket_name}/{OPENMETADATA_TEMPLATE_FILE_NAME}-{exc}"
                )
        return None

    def _is_metadata_file_present(self, bucket_name: str):
        return self.prefix_exits(
            bucket_name=bucket_name,
            prefix=OPENMETADATA_TEMPLATE_FILE_NAME,
        )

    def _generate_unstructured_container(
        self, bucket_response: S3BucketResponse
    ) -> S3ContainerDetails:
        return S3ContainerDetails(
            name=bucket_response.name,
            prefix=KEY_SEPARATOR,
            creation_date=bucket_response.creation_date.isoformat(),
            number_of_objects=self._fetch_metric(
                bucket_name=bucket_response.name, metric=S3Metric.NUMBER_OF_OBJECTS
            ),
            size=self._fetch_metric(
                bucket_name=bucket_response.name, metric=S3Metric.BUCKET_SIZE_BYTES
            ),
            file_formats=[],
            data_model=None,
            sourceUrl=self._get_bucket_source_url(bucket_name=bucket_response.name),
        )

    def _get_sample_file_path(
        self, bucket_name: str, metadata_entry: MetadataEntry
    ) -> Optional[str]:
        """
        Given a bucket and a metadata entry, returns the full path key to a file which can then be used to infer schema
        or None in the case of a non-structured metadata entry, or if no such keys can be found
        """
        prefix = self._get_sample_file_prefix(metadata_entry=metadata_entry)
        # no objects found in the data path
        if not self.prefix_exits(bucket_name=bucket_name, prefix=prefix):
            logger.warning(f"Ignoring metadata entry {prefix} - no files found")
            return None
        # this will look only in the first 1000 files under that path (default for list_objects_v2).
        # We'd rather not do pagination here as it would incur unwanted costs
        try:
            response = self.s3_client.list_objects_v2(Bucket=bucket_name, Prefix=prefix)
            candidate_keys = [
                entry["Key"]
                for entry in response[S3_CLIENT_ROOT_RESPONSE]
                if entry
                and entry.get("Key")
                and entry["Key"].endswith(metadata_entry.structureFormat)
            ]
            # pick a random key out of the candidates if any were returned
            if candidate_keys:
                result_key = secrets.choice(candidate_keys)
                logger.info(
                    f"File {result_key} was picked to infer data structure from."
                )
                return result_key
            logger.warning(
                f"No sample files found in {prefix} with {metadata_entry.structureFormat} extension"
            )
            return None
        except Exception:
            logger.debug(traceback.format_exc())
            logger.warning(
                f"Error when trying to list objects in S3 bucket {bucket_name} at prefix {prefix}"
            )
            return None

    def prefix_exits(self, bucket_name: str, prefix: str) -> bool:
        """
        Checks if a given prefix exists in a bucket
        """
        try:
            res = self.s3_client.list_objects_v2(
                Bucket=bucket_name, Prefix=prefix, MaxKeys=1
            )
            return S3_CLIENT_ROOT_RESPONSE in res
        except Exception:
            logger.debug(traceback.format_exc())
            logger.warning(
                f"Failed when trying to check if S3 prefix {prefix} exists in bucket {bucket_name}"
            )
            return False

    def get_aws_bucket_region(self, bucket_name: str) -> str:
        """
        Method to fetch the bucket region
        """
        region = None
        try:
            region_resp = self.s3_client.get_bucket_location(Bucket=bucket_name)
            region = region_resp.get("LocationConstraint")
        except Exception:
            logger.debug(traceback.format_exc())
            logger.warning(f"Unable to get the region for bucket: {bucket_name}")
        return region or self.service_connection.awsConfig.awsRegion

    def _get_bucket_source_url(self, bucket_name: str) -> Optional[str]:
        """
        Method to get the source url of s3 bucket
        """
        try:
            region = self.get_aws_bucket_region(bucket_name=bucket_name)
            return (
                f"https://s3.console.aws.amazon.com/s3/buckets/{bucket_name}"
                f"?region={region}&tab=objects"
            )
        except Exception as exc:
            logger.debug(traceback.format_exc())
            logger.error(f"Unable to get source url: {exc}")
        return None

    def _get_object_source_url(self, bucket_name: str, prefix: str) -> Optional[str]:
        """
        Method to get the source url of s3 bucket
        """
        try:
            region = self.get_aws_bucket_region(bucket_name=bucket_name)
            return (
                f"https://s3.console.aws.amazon.com/s3/buckets/{bucket_name}"
                f"?region={region}&prefix={prefix}/"
                f"&showversions=false"
            )
        except Exception as exc:
            logger.debug(traceback.format_exc())
            logger.error(f"Unable to get source url: {exc}")
        return None
