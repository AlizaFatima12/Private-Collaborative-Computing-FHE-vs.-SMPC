# Makefile for Private Collaborative Computing: FHE vs. SMPC
.PHONY: build up down logs verify-e2e benchmark benchmark-quick fhe smpc analysis report test demo gui clean

build:
	docker compose build

up:
	docker compose up -d --build node-1 node-2 node-3 coordinator

down:
	docker compose down

logs:
	docker compose logs -f node-1 node-2 node-3 coordinator

verify-e2e:
	docker compose exec coordinator python3 -m system.cli verify-e2e

benchmark:
	docker compose run --build --rm benchmark

benchmark-quick:
	docker compose run --build --rm benchmark-quick

fhe:
	docker compose run --rm fhe

smpc:
	docker compose run --rm smpc

analysis:
	docker compose run --rm analysis

test:
	docker compose run --rm test

demo:
	docker compose run --rm demo

gui:
	docker compose up gui

clean:
	rm -rf results/raw/* results/processed/* results/graphs/* results/reports/*
