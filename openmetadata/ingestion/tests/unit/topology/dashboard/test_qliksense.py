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
Test QlikSense using the topology
"""

from unittest import TestCase
from unittest.mock import patch

import pytest

from metadata.generated.schema.api.data.createChart import CreateChartRequest
from metadata.generated.schema.api.data.createDashboard import CreateDashboardRequest
from metadata.generated.schema.entity.services.dashboardService import (
    DashboardConnection,
    DashboardService,
    DashboardServiceType,
)
from metadata.generated.schema.metadataIngestion.workflow import (
    OpenMetadataWorkflowConfig,
)
from metadata.generated.schema.type.basic import FullyQualifiedEntityName
from metadata.ingestion.source.dashboard.qliksense.client import QlikSenseClient
from metadata.ingestion.source.dashboard.qliksense.metadata import QliksenseSource
from metadata.ingestion.source.dashboard.qliksense.models import (
    QlikDashboard,
    QlikSheet,
    QlikSheetInfo,
    QlikSheetMeta,
)

MOCK_DASHBOARD_SERVICE = DashboardService(
    id="c3eb265f-5445-4ad3-ba5e-797d3a3071bb",
    name="qliksense_source_test",
    fullyQualifiedName=FullyQualifiedEntityName(__root__="qliksense_source_test"),
    connection=DashboardConnection(),
    serviceType=DashboardServiceType.QlikSense,
)


mock_qliksense_config = {
    "source": {
        "type": "qliksense",
        "serviceName": "local_qliksensem",
        "serviceConnection": {
            "config": {
                "type": "QlikSense",
                "certificates": {
                    "rootCertificate": "/test/path/root.pem",
                    "clientKeyCertificate": "/test/path/client_key.pem",
                    "clientCertificate": "/test/path/client.pem",
                },
                "userDirectory": "demo",
                "userId": "demo",
                "hostPort": "wss://test:4747",
                "displayUrl": "https://test",
            }
        },
        "sourceConfig": {
            "config": {"dashboardFilterPattern": {}, "chartFilterPattern": {}}
        },
    },
    "sink": {"type": "metadata-rest", "config": {}},
    "workflowConfig": {
        "openMetadataServerConfig": {
            "hostPort": "http://localhost:8585/api",
            "authProvider": "openmetadata",
            "securityConfig": {
                "jwtToken": "eyJraWQiOiJHYjM4OWEtOWY3Ni1nZGpzLWE5MmotMDI0MmJrOTQzNTYiLCJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJhZG1pbiIsImlzQm90IjpmYWxzZSwiaXNzIjoib3Blbi1tZXRhZGF0YS5vcmciLCJpYXQiOjE2NjM5Mzg0NjIsImVtYWlsIjoiYWRtaW5Ab3Blbm1ldGFkYXRhLm9yZyJ9.tS8um_5DKu7HgzGBzS1VTA5uUjKWOCU0B_j08WXBiEC0mr0zNREkqVfwFDD-d24HlNEbrqioLsBuFRiwIWKc1m_ZlVQbG7P36RUxhuv2vbSp80FKyNM-Tj93FDzq91jsyNmsQhyNv_fNr3TXfzzSPjHt8Go0FMMP66weoKMgW2PbXlhVKwEuXUHyakLLzewm9UMeQaEiRzhiTMU3UkLXcKbYEJJvfNFcLwSl9W8JCO_l0Yj3ud-qt_nQYEZwqW6u5nfdQllN133iikV4fM5QZsMCnm8Rq1mvLR0y9bmJiD7fwM1tmJ791TUWqmKaTnP49U493VanKpUAfzIiOiIbhg"
            },
        }
    },
}
MOCK_DASHBOARD_NAME = "New Dashboard"

MOCK_DASHBOARD_DETAILS = QlikDashboard(
    qDocName=MOCK_DASHBOARD_NAME,
    qDocId="1",
    qTitle=MOCK_DASHBOARD_NAME,
)

MOCK_CHARTS = [
    QlikSheet(
        qInfo=QlikSheetInfo(qId="11"), qMeta=QlikSheetMeta(title="Top Salespeople")
    ),
    QlikSheet(
        qInfo=QlikSheetInfo(qId="12"),
        qMeta=QlikSheetMeta(title="Milan Datasets", description="dummy"),
    ),
]

EXPECTED_DASHBOARD = CreateDashboardRequest(
    name="1",
    displayName="New Dashboard",
    sourceUrl="https://test/sense/app/1/overview",
    charts=[],
    tags=None,
    owner=None,
    service="qliksense_source_test",
    extension=None,
)

EXPECTED_DASHBOARDS = [
    CreateChartRequest(
        name="11",
        displayName="Top Salespeople",
        chartType="Other",
        sourceUrl="https://test/sense/app/1/sheet/11",
        tags=None,
        owner=None,
        service="qliksense_source_test",
    ),
    CreateChartRequest(
        name="12",
        displayName="Milan Datasets",
        chartType="Other",
        sourceUrl="https://test/sense/app/1/sheet/12",
        tags=None,
        owner=None,
        service="qliksense_source_test",
        description="dummy",
    ),
]


class QlikSenseUnitTest(TestCase):
    """
    Implements the necessary methods to extract
    QlikSense Unit Test
    """

    def __init__(self, methodName) -> None:
        with patch.object(
            QlikSenseClient, "get_dashboard_for_test_connection", return_value=None
        ):
            super().__init__(methodName)
            # test_connection.return_value = False
            self.config = OpenMetadataWorkflowConfig.parse_obj(mock_qliksense_config)
            self.qliksense = QliksenseSource.create(
                mock_qliksense_config["source"],
                self.config.workflowConfig.openMetadataServerConfig,
            )
            self.qliksense.context.__dict__[
                "dashboard_service"
            ] = MOCK_DASHBOARD_SERVICE

    @pytest.mark.order(1)
    def test_dashboard(self):
        dashboard_list = []
        results = self.qliksense.yield_dashboard(MOCK_DASHBOARD_DETAILS)
        for result in results:
            if isinstance(result, CreateDashboardRequest):
                dashboard_list.append(result)
        self.assertEqual(EXPECTED_DASHBOARD, dashboard_list[0])

    @pytest.mark.order(2)
    def test_dashboard_name(self):
        assert (
            self.qliksense.get_dashboard_name(MOCK_DASHBOARD_DETAILS)
            == MOCK_DASHBOARD_NAME
        )

    @pytest.mark.order(3)
    def test_chart(self):
        dashboard_details = MOCK_DASHBOARD_DETAILS
        with patch.object(
            QlikSenseClient, "get_dashboard_charts", return_value=MOCK_CHARTS
        ):
            results = list(self.qliksense.yield_dashboard_chart(dashboard_details))
            chart_list = []
            for result in results:
                if isinstance(result, CreateChartRequest):
                    chart_list.append(result)
            for _, (expected, original) in enumerate(
                zip(EXPECTED_DASHBOARDS, chart_list)
            ):
                self.assertEqual(expected, original)
