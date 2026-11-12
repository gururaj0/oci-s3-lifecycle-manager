output "api_endpoint" { value = oci_apigateway_deployment.demo.endpoint }
output "gateway_hostname" { value = oci_apigateway_gateway.demo.hostname }
output "instance_id" { value = oci_core_instance.backend.id }
output "private_ip" { value = oci_core_instance.backend.private_ip }
output "openapi_url" { value = "https://${oci_apigateway_gateway.demo.hostname}/openapi.json" }
output "mode" { value = "mock; production readiness requires M0 integration gates" }
output "readiness_url" { value = "https://${oci_apigateway_gateway.demo.hostname}/health/ready" }
output "cleanup_runbook" { value = "docs/operations.md in release source" }
