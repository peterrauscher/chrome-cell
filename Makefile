IMAGE ?= ghcr.io/peterrauscher/chrome-cell
TAG ?= local
PLATFORM ?= linux/amd64

.PHONY: build run push k8s-apply k8s-delete

build:
	docker build --platform=$(PLATFORM) -t $(IMAGE):$(TAG) .
	docker build --platform=$(PLATFORM) -t $(IMAGE)-gateway:$(TAG) gateway

run:
	docker compose up --build

push:
	docker push $(IMAGE):$(TAG)
	docker push $(IMAGE)-gateway:$(TAG)

k8s-apply:
	kubectl apply -k k8s/overlays/homelab

k8s-delete:
	kubectl delete -k k8s/overlays/homelab
