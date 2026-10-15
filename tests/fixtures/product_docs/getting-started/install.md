---
title: Install Open Net Worth
description: Get the app running locally in under five minutes.
category: getting-started
order: 10
audience: [user, operator]
status: stable
related: [features/budgeting]
keywords: [install, setup, docker]
---
# Install Open Net Worth

Open Net Worth runs on your own machine. This page walks through getting the
backend and frontend running for the first time.

## Prerequisites

You need Python 3.12 and Node 20 installed before you start. If prices don't show up
once you're running, see [Prices are not updating](../troubleshooting/prices-not-updating.md).

## Running the backend

Start the backend with `uvicorn backend.main:app --reload --port 8080`.

## Running the backend

A second, identical heading — used to prove duplicate anchors get a `-1` suffix
the way GitHub's own heading slugs do.
