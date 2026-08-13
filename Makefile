.PHONY: test smoke install run

test:
	python -m pytest

smoke:
	python -m cleanroom.cli inspect examples/sample.md

install:
	python -m pip install -e '.[dev,pdf]'

run:
	python -m cleanroom.web --port 8080