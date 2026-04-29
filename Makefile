HOST ?= ubuntu@54.245.246.129
FLAKE_REF ?= .#default

.PHONY: help build check activate diff deploy

help:
	@echo "twitch-mc make targets"
	@echo "  build       — nix build the system-manager closure (sanity check)"
	@echo "  check       — flake check"
	@echo "  activate    — activate locally (needs Linux + sudo). Run on the EC2 box."
	@echo "  diff        — print what activate would change"
	@echo "  deploy      — git pull + activate over ssh on \$$HOST"

build:
	nix build $(FLAKE_REF) --print-build-logs

check:
	nix flake check

activate:
	nix run github:numtide/system-manager -- switch --flake $(FLAKE_REF) --sudo

diff:
	nix run github:numtide/system-manager -- diff --flake $(FLAKE_REF) || true

deploy:
	ssh $(HOST) 'cd ~/twitch-mc && git pull --ff-only && \
		nix run github:numtide/system-manager -- switch --flake .#default --sudo'
