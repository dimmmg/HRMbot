.PHONY: run test check compose-up compose-down clean

run:
	python -m app.bot

test:
	python -m pytest tests/

check: test
	@echo "All checks passed!"

compose-up:
	docker compose up --build -d

compose-down:
	docker compose down

clean:
	rm -rf __pycache__ .pytest_cache logs/*.log