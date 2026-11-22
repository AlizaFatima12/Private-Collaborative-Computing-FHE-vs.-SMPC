# Makefile for Private Collaborative Computing: FHE vs. SMPC
.PHONY: build benchmark benchmark-quick fhe smpc analysis report test clean

build:
	docker compose build

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

clean:
	rm -rf results/raw/* results/processed/* results/graphs/* results/reports/*
