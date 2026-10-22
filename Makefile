PYTHON ?= python3
BUILD_OPTIONS ?=
VERIFY_OPTIONS ?=
VERSION = $(shell "$(PYTHON)" -c 'from quota_widget import __version__; print(__version__)')

.PHONY: help dev test build deb verify-dist artifacts

help:
	@echo "make dev          Install the editable app and build tooling in your active venv"
	@echo "make test         Run all unit tests with Qt offscreen"
	@echo "make build        Build wheel and source distribution in dist/"
	@echo "make deb          Build Python distributions and the Ubuntu .deb"
	@echo "make verify-dist  Verify dist/ and rebuild wheel/.deb from the source archive"
	@echo "make artifacts    Build and verify all artifacts; write dist/SHA256SUMS"
	@echo "Use PYTHON=/path/to/venv/bin/python to choose an interpreter."

dev:
	"$(PYTHON)" -m pip install -e . -r requirements-dev.txt

test:
	QT_QPA_PLATFORM=offscreen QT_QPA_PLATFORMTHEME=generic "$(PYTHON)" -c 'from PySide6.QtWidgets import QApplication; app = QApplication([]); print("Qt offscreen preflight passed")'
	QT_QPA_PLATFORM=offscreen QT_QPA_PLATFORMTHEME=generic "$(PYTHON)" -m unittest discover -s tests -v

build:
	"$(PYTHON)" -m build $(BUILD_OPTIONS) --outdir dist

deb: build
	"$(PYTHON)" packaging/build_deb.py "dist/quota_widget-$(VERSION)-py3-none-any.whl" --output-dir dist

verify-dist:
	"$(PYTHON)" packaging/verify_dist.py --directory dist --rebuild-sdist $(VERIFY_OPTIONS)

artifacts: deb
	"$(PYTHON)" packaging/verify_dist.py --directory dist --rebuild-sdist $(VERIFY_OPTIONS)
