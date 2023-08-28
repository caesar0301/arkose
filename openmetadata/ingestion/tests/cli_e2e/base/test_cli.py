#  Copyright 2022 Collate
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
Test database connectors with CLI
"""
import os
import re
import subprocess
from abc import ABC, abstractmethod
from ast import literal_eval
from pathlib import Path

import yaml

from metadata.config.common import load_config_file
from metadata.ingestion.api.sink import SinkStatus
from metadata.ingestion.api.source import SourceStatus
from metadata.ingestion.api.workflow import Workflow
from metadata.ingestion.ometa.ometa_api import OpenMetadata
from metadata.utils.constants import UTF_8

from .config_builders.builders import builder_factory
from .e2e_types import E2EType

PATH_TO_RESOURCES = os.path.dirname(Path(os.path.realpath(__file__)).parent)

REGEX_AUX = {"log": r"\s+\[[^]]+]\s+[A-Z]+\s+[^}]+}\s+-\s+"}


class CliBase(ABC):
    """
    CLI Base class
    """

    openmetadata: OpenMetadata
    test_file_path: str
    config_file_path: str

    def run_command(self, command: str = "ingest", test_file_path=None) -> str:
        file_path = (
            test_file_path if test_file_path is not None else self.test_file_path
        )
        args = [
            "metadata",
            command,
            "-c",
            file_path,
        ]
        process_status = subprocess.Popen(args, stderr=subprocess.PIPE)
        _, stderr = process_status.communicate()
        return stderr.decode("utf-8")

    def retrieve_lineage(self, entity_fqn: str) -> dict:
        return self.openmetadata.client.get(
            f"/lineage/table/name/{entity_fqn}?upstreamDepth=3&downstreamDepth=3"
        )

    def build_config_file(
        self, test_type: E2EType = E2EType.INGEST, extra_args: dict = None
    ) -> None:
        with open(self.config_file_path, encoding=UTF_8) as config_file:
            config_yaml = yaml.safe_load(config_file)
            config_yaml = self.build_yaml(config_yaml, test_type, extra_args)
            with open(self.test_file_path, "w", encoding=UTF_8) as test_file:
                yaml.dump(config_yaml, test_file)

    def retrieve_statuses(self, result):
        source_status: SourceStatus = self.extract_source_status(result)
        sink_status: SinkStatus = self.extract_sink_status(result)
        return sink_status, source_status

    @staticmethod
    def get_workflow(connector: str, test_type: str) -> Workflow:
        config_file = Path(
            PATH_TO_RESOURCES + f"/{test_type}/{connector}/{connector}.yaml"
        )
        config_dict = load_config_file(config_file)
        return Workflow.create(config_dict)

    @staticmethod
    def extract_source_status(output) -> SourceStatus:
        output_clean = output.replace("\n", " ")
        output_clean = re.sub(" +", " ", output_clean)
        output_clean_ansi = re.compile(r"\x1b[^m]*m")
        output_clean = output_clean_ansi.sub(" ", output_clean)
        regex = r"Source Status:%(log)s(.*?)%(log)sSink Status: .*" % REGEX_AUX
        output_clean = re.findall(regex, output_clean.strip())
        return SourceStatus.parse_obj(literal_eval(output_clean[0].strip()))

    @staticmethod
    def extract_sink_status(output) -> SinkStatus:
        output_clean = output.replace("\n", " ")
        output_clean = re.sub(" +", " ", output_clean)
        output_clean_ansi = re.compile(r"\x1b[^m]*m")
        output_clean = output_clean_ansi.sub("", output_clean)
        if re.match(".* Processor Status: .*", output_clean):
            regex = r"Sink Status:%(log)s(.*?)%(log)sProcessor Status: .*" % REGEX_AUX
            output_clean = re.findall(regex, output_clean.strip())[0].strip()
        else:
            regex = r".*Sink Status:%(log)s(.*?)%(log)sWorkflow Summary.*" % REGEX_AUX
            output_clean = re.findall(regex, output_clean.strip())[0].strip()
        return SinkStatus.parse_obj(literal_eval(output_clean))

    @staticmethod
    def build_yaml(config_yaml: dict, test_type: E2EType, extra_args: dict):
        """
        Build yaml as per E2EType
        """

        builder = builder_factory(
            test_type.value,
            config_yaml,
            extra_args,
        )

        return builder.build()

    @staticmethod
    @abstractmethod
    def get_test_type():
        pass
