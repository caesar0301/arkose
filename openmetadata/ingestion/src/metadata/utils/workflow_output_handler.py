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
Module handles the output messages from different workflows
"""

import time
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Type, Union

from pydantic import BaseModel
from tabulate import tabulate

from metadata.config.common import ConfigurationError
from metadata.generated.schema.metadataIngestion.workflow import LogLevels
from metadata.ingestion.api.parser import (
    InvalidWorkflowException,
    ParsingConfigurationError,
)
from metadata.ingestion.api.status import StackTraceError, Status
from metadata.utils.constants import UTF_8
from metadata.utils.helpers import pretty_print_time_duration
from metadata.utils.logger import ANSI, log_ansi_encoded_string

WORKFLOW_FAILURE_MESSAGE = "Workflow finished with failures"
WORKFLOW_WARNING_MESSAGE = "Workflow finished with warnings"
WORKFLOW_SUCCESS_MESSAGE = "Workflow finished successfully"


class Failure(BaseModel):
    """
    Auxiliary class to print the error per status
    """

    name: str
    failures: List[StackTraceError]


class Summary(BaseModel):
    """
    Auxiliary class to calculate the summary of all statuses
    """

    records = 0
    warnings = 0
    errors = 0
    filtered = 0

    def __add__(self, other):
        self.records += other.records
        self.warnings += other.warnings
        self.errors += other.errors
        self.filtered += other.filtered
        return self


class WorkflowType(Enum):
    """
    Workflow type enums
    """

    INGEST = "ingest"
    PROFILE = "profile"
    TEST = "test"
    LINEAGE = "lineage"
    USAGE = "usage"
    INSIGHT = "insight"


EXAMPLES_WORKFLOW_PATH: Path = Path(__file__).parent / "../examples" / "workflows"

URLS = {
    WorkflowType.INGEST: "https://docs.open-metadata.org/connectors/ingestion/workflows/metadata",
    WorkflowType.PROFILE: "https://docs.open-metadata.org/connectors/ingestion/workflows/profiler",
    WorkflowType.TEST: "https://docs.open-metadata.org/connectors/ingestion/workflows/data-quality",
    WorkflowType.LINEAGE: "https://docs.open-metadata.org/connectors/ingestion/workflows/lineage",
    WorkflowType.USAGE: "https://docs.open-metadata.org/connectors/ingestion/workflows/usage",
}

DEFAULT_EXAMPLE_FILE = {
    WorkflowType.INGEST: "bigquery",
    WorkflowType.PROFILE: "bigquery_profiler",
    WorkflowType.TEST: "test_suite",
    WorkflowType.LINEAGE: "bigquery_lineage",
    WorkflowType.USAGE: "bigquery_usage",
}


def print_more_info(workflow_type: WorkflowType) -> None:
    """
    Print more information message
    """
    log_ansi_encoded_string(
        message=f"\nFor more information, please visit: {URLS[workflow_type]}"
        "\nOr join us in Slack: https://slack.open-metadata.org/"
    )


def print_error_msg(msg: str) -> None:
    """
    Print message with error style
    """
    log_ansi_encoded_string(color=ANSI.BRIGHT_RED, bold=False, message=f"{msg}")


def calculate_ingestion_type(source_type_name: str) -> WorkflowType:
    """
    Calculates the ingestion type depending on the source type name
    """
    if source_type_name.endswith("lineage"):
        return WorkflowType.LINEAGE
    if source_type_name.endswith("usage"):
        return WorkflowType.USAGE
    return WorkflowType.INGEST


def calculate_example_file(source_type_name: str, workflow_type: WorkflowType) -> str:
    """
    Calculates the ingestion type depending on the source type name and workflow_type
    """
    if workflow_type == WorkflowType.USAGE:
        return f"{source_type_name}_usage"
    if workflow_type == WorkflowType.LINEAGE:
        return f"{source_type_name}_lineage"
    if workflow_type == WorkflowType.PROFILE:
        return f"{source_type_name}_profiler"
    if workflow_type == WorkflowType.TEST:
        return DEFAULT_EXAMPLE_FILE[workflow_type]
    return source_type_name


def print_file_example(source_type_name: str, workflow_type: WorkflowType):
    """
    Print an example file for a given configuration
    """
    if source_type_name is not None:
        example_file = calculate_example_file(source_type_name, workflow_type)
        example_path = EXAMPLES_WORKFLOW_PATH / f"{example_file}.yaml"
        if not example_path.exists():
            example_file = DEFAULT_EXAMPLE_FILE[workflow_type]
            example_path = EXAMPLES_WORKFLOW_PATH / f"{example_file}.yaml"
        log_ansi_encoded_string(
            message=f"\nMake sure you are following the following format e.g. '{example_file}':"
        )
        log_ansi_encoded_string(message="------------")
        with open(example_path, encoding=UTF_8) as file:
            log_ansi_encoded_string(message=file.read())
        log_ansi_encoded_string(message="------------")


def print_init_error(
    exc: Union[Exception, Type[Exception]],
    config: dict,
    workflow_type: WorkflowType = WorkflowType.INGEST,
) -> None:
    """
    Print a workflow initialization error
    """
    source_type_name = None
    if (
        config
        and config.get("source", None) is not None
        and config["source"].get("type", None) is not None
    ):
        source_type_name = config["source"].get("type")
        source_type_name = source_type_name.replace("-", "-")
        workflow_type = (
            calculate_ingestion_type(source_type_name)
            if workflow_type == WorkflowType.INGEST
            else workflow_type
        )

    if isinstance(
        exc, (ParsingConfigurationError, ConfigurationError, InvalidWorkflowException)
    ):
        print_error_msg(f"Error loading {workflow_type.name} configuration: {exc}")
        print_file_example(source_type_name, workflow_type)
        print_more_info(workflow_type)
    else:
        print_error_msg(f"\nError initializing {workflow_type.name}: {exc}")
        print_more_info(workflow_type)


def print_status(workflow) -> None:
    """
    Print the workflow results
    """

    print_workflow_summary(workflow, source=True, stage=True, bulk_sink=True)

    if workflow.source.get_status().source_start_time:
        log_ansi_encoded_string(
            color=ANSI.BRIGHT_CYAN,
            bold=True,
            message="Workflow finished in time: "
            f"{pretty_print_time_duration(time.time()-workflow.source.get_status().source_start_time)}",
        )

    if workflow.result_status() == 1:
        log_ansi_encoded_string(
            color=ANSI.BRIGHT_RED,
            bold=True,
            message=WORKFLOW_FAILURE_MESSAGE,
        )
    elif workflow.source.get_status().warnings or (
        hasattr(workflow, "sink") and workflow.sink.get_status().warnings
    ):
        log_ansi_encoded_string(
            color=ANSI.YELLOW, bold=True, message=WORKFLOW_WARNING_MESSAGE
        )
    else:
        log_ansi_encoded_string(
            color=ANSI.GREEN, bold=True, message=WORKFLOW_SUCCESS_MESSAGE
        )


def print_profiler_status(workflow) -> None:
    """
    Print the profiler workflow results
    """
    print_workflow_summary(
        workflow,
        source=True,
        processor=True,
        source_status=workflow.source_status,
    )

    if workflow.source_status.source_start_time:
        log_ansi_encoded_string(
            color=ANSI.BRIGHT_CYAN,
            bold=True,
            message="Workflow finished in time: "
            f"{pretty_print_time_duration(time.time()-workflow.source_status.source_start_time)}",
        )

    if workflow.result_status() == 1:
        log_ansi_encoded_string(
            color=ANSI.BRIGHT_RED, bold=True, message=WORKFLOW_FAILURE_MESSAGE
        )
    elif workflow.source_status.warnings or (
        hasattr(workflow, "sink") and workflow.sink.get_status().warnings
    ):
        log_ansi_encoded_string(
            color=ANSI.YELLOW, bold=True, message=WORKFLOW_WARNING_MESSAGE
        )
    else:
        log_ansi_encoded_string(
            color=ANSI.GREEN, bold=True, message=WORKFLOW_SUCCESS_MESSAGE
        )


def print_test_suite_status(workflow) -> None:
    """
    Print the test suite workflow results
    """
    print_workflow_summary(workflow, processor=True, processor_status=workflow.status)

    if workflow.result_status() == 1:
        log_ansi_encoded_string(
            color=ANSI.BRIGHT_RED, bold=True, message=WORKFLOW_FAILURE_MESSAGE
        )
    else:
        log_ansi_encoded_string(
            color=ANSI.GREEN, bold=True, message=WORKFLOW_SUCCESS_MESSAGE
        )


def print_data_insight_status(workflow) -> None:
    """
    Print the test suite workflow results
    Args:
        workflow (DataInsightWorkflow): workflow object
    """
    print_workflow_summary(
        workflow,
        processor=True,
        processor_status=workflow.status,
    )

    if workflow.source.get_status().source_start_time:
        log_ansi_encoded_string(
            message=f"Workflow finished in time {pretty_print_time_duration(time.time()-workflow.source.get_status().source_start_time)} ",  # pylint: disable=line-too-long
        )

    if workflow.result_status() == 1:
        log_ansi_encoded_string(message=WORKFLOW_FAILURE_MESSAGE)
    elif (
        workflow.source.get_status().warnings
        or workflow.status.warnings
        or (hasattr(workflow, "sink") and workflow.sink.get_status().warnings)
    ):
        log_ansi_encoded_string(message=WORKFLOW_WARNING_MESSAGE)
    else:
        log_ansi_encoded_string(message=WORKFLOW_SUCCESS_MESSAGE)
        log_ansi_encoded_string(
            color=ANSI.GREEN, bold=True, message=WORKFLOW_SUCCESS_MESSAGE
        )


def is_debug_enabled(workflow) -> bool:
    return (
        hasattr(workflow, "config")
        and hasattr(workflow.config, "workflowConfig")
        and hasattr(workflow.config.workflowConfig, "loggerLevel")
        and workflow.config.workflowConfig.loggerLevel is LogLevels.DEBUG
    )


def get_source_status(workflow, source_status: Status) -> Optional[Status]:
    if hasattr(workflow, "source"):
        return source_status if source_status else workflow.source.get_status()
    return source_status


def get_processor_status(workflow, processor_status: Status) -> Optional[Status]:
    if hasattr(workflow, "processor"):
        return processor_status if processor_status else workflow.processor.get_status()
    return processor_status


def print_workflow_summary(
    workflow,
    source: bool = False,
    stage: bool = False,
    bulk_sink: bool = False,
    processor: bool = False,
    source_status: Status = None,
    processor_status: Status = None,
):
    """
    Args:
        workflow: the workflow status to be printed
        source: if source status must be printed
        bulk_sink: if bull_sink status must be printed
        processor: if processor status must be printed
        stage: if stage status must be printed
        source_status: alternative source status to be printed in case is different to the default of the workflow
        processor_status: alternative processor status to be printed in case is different to the default of the workflow

    Returns:
        Print Workflow status when the workflow logger level is DEBUG
    """
    source_status = get_source_status(workflow, source_status)
    processor_status = get_processor_status(workflow, processor_status)
    if is_debug_enabled(workflow):
        print_workflow_status_debug(
            workflow,
            bulk_sink,
            stage,
            source_status,
            processor_status,
        )
    summary = Summary()
    failures = []
    if source_status and source:
        summary += get_summary(source_status)
        failures.append(Failure(name="Source", failures=source_status.failures))
    if hasattr(workflow, "stage") and stage:
        summary += get_summary(workflow.stage.get_status())
        failures.append(
            Failure(name="Stage", failures=workflow.stage.get_status().failures)
        )
    if hasattr(workflow, "sink"):
        summary += get_summary(workflow.sink.get_status())
        failures.append(
            Failure(name="Sink", failures=workflow.sink.get_status().failures)
        )
    if hasattr(workflow, "bulk_sink") and bulk_sink:
        summary += get_summary(workflow.bulk_sink.get_status())
        failures.append(
            Failure(name="Bulk Sink", failures=workflow.bulk_sink.get_status().failures)
        )
    if processor_status and processor:
        summary += get_summary(processor_status)
        failures.append(Failure(name="Processor", failures=processor_status.failures))

    print_failures_if_apply(failures)

    log_ansi_encoded_string(bold=True, message="Workflow Summary:")
    log_ansi_encoded_string(message=f"Total processed records: {summary.records}")
    log_ansi_encoded_string(message=f"Total warnings: {summary.warnings}")
    log_ansi_encoded_string(message=f"Total filtered: {summary.filtered}")
    log_ansi_encoded_string(message=f"Total errors: {summary.errors}")

    total_success = max(summary.records, 1)
    log_ansi_encoded_string(
        color=ANSI.BRIGHT_CYAN,
        bold=True,
        message=f"Success %: "
        f"{round(total_success * 100 / (total_success + summary.errors), 2)}",
    )


def print_workflow_status_debug(
    workflow,
    bulk_sink: bool = False,
    stage: bool = False,
    source_status: Status = None,
    processor_status: Status = None,
) -> None:
    """
    Args:
        workflow: the workflow status to be printed
        bulk_sink: if bull_sink status must be printed
        stage: if stage status must be printed
        source_status: source status to be printed
        processor_status: processor status to be printed

    Returns:
        Print Workflow status when the workflow logger level is DEBUG
    """
    log_ansi_encoded_string(bold=True, message="Statuses detailed info:")
    if source_status:
        log_ansi_encoded_string(bold=True, message="Source Status:")
        log_ansi_encoded_string(message=source_status.as_string())
    if hasattr(workflow, "stage") and stage:
        log_ansi_encoded_string(bold=True, message="Stage Status:")
        log_ansi_encoded_string(message=workflow.stage.get_status().as_string())
    if hasattr(workflow, "sink"):
        log_ansi_encoded_string(bold=True, message="Sink Status:")
        log_ansi_encoded_string(message=workflow.sink.get_status().as_string())
    if hasattr(workflow, "bulk_sink") and bulk_sink:
        log_ansi_encoded_string(bold=True, message="Bulk Sink Status:")
        log_ansi_encoded_string(message=workflow.bulk_sink.get_status().as_string())
    if processor_status:
        log_ansi_encoded_string(bold=True, message="Processor Status:")
        log_ansi_encoded_string(message=processor_status.as_string())


def get_summary(status: Status) -> Summary:
    records = len(status.records)
    warnings = len(status.warnings)
    errors = len(status.failures)
    filtered = 0
    if hasattr(status, "filtered"):
        filtered = len(status.filtered)
    return Summary(records=records, warnings=warnings, errors=errors, filtered=filtered)


def get_failures(failure: Failure) -> List[Dict[str, str]]:
    return [
        {
            "From": failure.name,
            "Entity Name": f.name,
            "Message": f.error,
            "Stack Trace": f.stack_trace,
        }
        for f in failure.failures
    ]


def print_failures_if_apply(failures: List[Failure]) -> None:
    # take only the ones that contain failures
    failures = [f for f in failures if f.failures]
    if failures:
        # create a list of dictionaries' list
        all_data = [get_failures(failure) for failure in failures]
        # create a single of dictionaries
        data = [f for fs in all_data for f in fs]
        # creat a dictionary with a key and a list of values from the list
        error_table = {k: [dic[k] for dic in data] for k in data[0]}
        if len(list(error_table.items())[0][1]) > 100:
            log_ansi_encoded_string(
                bold=True, message="Showing only the first 100 failures:"
            )
            # truncate list if number of values are over 100
            error_table = {k: v[:100] for k, v in error_table.items()}
        else:
            log_ansi_encoded_string(bold=True, message="List of failures:")

        log_ansi_encoded_string(
            message=f"\n{tabulate(error_table, headers='keys', tablefmt='grid')}"
        )
