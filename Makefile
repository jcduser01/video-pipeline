# video-pipeline — common tasks. Run `make` (or `make help`) for the list.

.DEFAULT_GOAL := help
.PHONY: help ready test safezone

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[1m%-10s\033[0m %s\n", $$1, $$2}'

ready:  ## Rebuild the environment after a fresh pull (Python extras + Remotion deps)
	uv sync --all-extras          # installs reframe + roughcut (mlx-whisper) + dev
	cd remotion && npm install    # caption-overlay renderer deps
	@echo "Ready. Native steps (transcription, ffmpeg render, Remotion) can now run."

test:  ## Run the test suite (pure logic; no native toolchain needed)
	uv run python -m unittest discover -s tests -t .

safezone:  ## Regenerate the Reels safe-zone spec from its template PNG
	uv run video-pipeline safezone-gen config/safezone/instagram-safe-zone-reels-9x16.png \
		--profile reels-9x16 -o config/safezone/reels-9x16.safezone.json
