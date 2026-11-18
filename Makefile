IMAGE ?= shop-api:0.1.0
CLUSTER ?= slo-demo

.PHONY: test rules cluster image stack deploy demo-rules prod-rules fault-latency fault-errors fault-one-pod fault-clear alerts verify-ha grafana destroy

test:
	python3 -m pytest -q tests

rules:                ## regenerate rules + dashboard from slo/slo.yaml
	python3 slo/generate_rules.py

cluster:
	kind create cluster --name $(CLUSTER) --config kind-config.yaml

image:
	docker build -t $(IMAGE) app
	kind load docker-image $(IMAGE) --name $(CLUSTER)

stack:                ## Prometheus/Alertmanager/Grafana, Loki, Tempo, OTel collector
	helm/install.sh

deploy:               ## app, loadgen, alert sink, rules, dashboard
	kubectl apply -f k8s/00-namespaces.yaml
	kubectl apply -f k8s/30-alert-sink.yaml -f k8s/40-otel-servicemonitor.yaml -f k8s/50-meta-rules.yaml
	kubectl apply -f k8s/slo-rules-prod.yaml -f k8s/dashboard-slo.yaml
	kubectl apply -f k8s/10-shop-api.yaml -f k8s/20-loadgen.yaml
	kubectl -n shop rollout status deploy/shop-api --timeout=180s

demo-rules:           ## compressed burn-rate windows (10m/1m, 30m/5m) so alerts fire within minutes
	kubectl apply -f k8s/slo-rules-demo.yaml

prod-rules:           ## back to production windows (1h/5m, 6h/30m, 1d/2h, 3d/6h)
	kubectl apply -f k8s/slo-rules-prod.yaml

fault-latency:        ## +900 ms in payments on all pods
	scripts/inject_fault.sh latency 900

fault-errors:         ## 40% payment failures on all pods
	scripts/inject_fault.sh errors 0.4

fault-one-pod:        ## 60% payment failures on a single pod
	scripts/inject_fault.sh errors 0.6 1

fault-clear:
	scripts/inject_fault.sh clear

alerts:
	scripts/check_alerts.sh

verify-ha:
	scripts/verify_ha.sh

grafana:              ## http://localhost:3000  (admin / admin)
	kubectl -n observability port-forward svc/kps-grafana 3000:80

destroy:
	kind delete cluster --name $(CLUSTER)
