param location string = resourceGroup().location
param monitorWorkspaceName string
param grafanaName string
param prometheusDceName string
param prometheusDcrName string
param workloadNsgName string
param natGatewayName string
param natPublicIpName string
param collectorAddress string
param workerAddress string

resource workloadNsg 'Microsoft.Network/networkSecurityGroups@2024-05-01' existing = {
  name: workloadNsgName
}

resource observabilityScrapeRule 'Microsoft.Network/networkSecurityGroups/securityRules@2024-05-01' = {
  parent: workloadNsg
  name: 'allow-observability-collector-scrapes'
  properties: {
    priority: 105
    access: 'Allow'
    direction: 'Inbound'
    protocol: 'Tcp'
    sourceAddressPrefix: collectorAddress
    sourcePortRange: '*'
    destinationAddressPrefix: 'VirtualNetwork'
    destinationPortRanges: [
      '9100'
      '9108'
    ]
  }
}

resource workerInboundDenyRule 'Microsoft.Network/networkSecurityGroups/securityRules@2024-05-01' = {
  parent: workloadNsg
  name: 'deny-cutover-worker-inbound'
  properties: {
    priority: 110
    access: 'Deny'
    direction: 'Inbound'
    protocol: '*'
    sourceAddressPrefix: 'VirtualNetwork'
    sourcePortRange: '*'
    destinationAddressPrefix: workerAddress
    destinationPortRange: '*'
  }
}

resource natPublicIp 'Microsoft.Network/publicIPAddresses@2024-05-01' = {
  name: natPublicIpName
  location: location
  sku: {
    name: 'Standard'
  }
  properties: {
    publicIPAllocationMethod: 'Static'
  }
}

resource natGateway 'Microsoft.Network/natGateways@2024-05-01' = {
  name: natGatewayName
  location: location
  sku: {
    name: 'Standard'
  }
  properties: {
    idleTimeoutInMinutes: 10
    publicIpAddresses: [
      {
        id: natPublicIp.id
      }
    ]
  }
}

resource monitorWorkspace 'Microsoft.Monitor/accounts@2025-10-03' = {
  name: monitorWorkspaceName
  location: location
  properties: {
    metrics: {
      enableAccessUsingResourcePermissions: true
    }
    publicNetworkAccess: 'Enabled'
  }
}

resource prometheusDce 'Microsoft.Insights/dataCollectionEndpoints@2023-03-11' = {
  name: prometheusDceName
  location: location
  properties: {
    networkAcls: {
      publicNetworkAccess: 'Enabled'
    }
  }
}

resource prometheusDcr 'Microsoft.Insights/dataCollectionRules@2024-03-11' = {
  name: prometheusDcrName
  location: location
  properties: {
    dataCollectionEndpointId: prometheusDce.id
    destinations: {
      monitoringAccounts: [
        {
          accountResourceId: monitorWorkspace.id
          name: 'zavaAzureMonitorWorkspace'
        }
      ]
    }
    dataFlows: [
      {
        streams: [
          'Microsoft-PrometheusMetrics'
        ]
        destinations: [
          'zavaAzureMonitorWorkspace'
        ]
      }
    ]
    description: 'Receives Zava Prometheus metrics through managed-identity remote write.'
  }
}

resource grafana 'Microsoft.Dashboard/grafana@2024-10-01' = {
  name: grafanaName
  location: location
  sku: {
    name: 'Standard'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    apiKey: 'Disabled'
    deterministicOutboundIP: 'Enabled'
    publicNetworkAccess: 'Enabled'
    grafanaIntegrations: {
      azureMonitorWorkspaceIntegrations: [
        {
          azureMonitorWorkspaceResourceId: monitorWorkspace.id
        }
      ]
    }
  }
}

var monitoringDataReaderRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  'b0d8363b-8ddd-447d-831f-62ca05bff136'
)

resource grafanaMetricsReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(monitorWorkspace.id, grafana.id, monitoringDataReaderRoleId)
  scope: monitorWorkspace
  properties: {
    principalId: grafana.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: monitoringDataReaderRoleId
  }
}

output monitorWorkspaceResourceId string = monitorWorkspace.id
output prometheusDcrResourceId string = prometheusDcr.id
output prometheusDcrImmutableId string = prometheusDcr.properties.immutableId
output prometheusMetricsIngestionEndpoint string = prometheusDce.properties.metricsIngestion.endpoint
output prometheusQueryEndpoint string = monitorWorkspace.properties.metrics.prometheusQueryEndpoint
output grafanaEndpoint string = grafana.properties.endpoint
output natGatewayResourceId string = natGateway.id
