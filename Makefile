# Makefile for Distributed Private Collaborative Computing System
.PHONY: build up down logs verify-e2e test clean

build:
	docker compose build

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f node-1 node-2 node-3 coordinator

verify-e2e:
	docker compose exec coordinator python3 -m system.cli verify-e2e

test:
	docker compose run --rm test

clean:
	rm -rf volumes/*
