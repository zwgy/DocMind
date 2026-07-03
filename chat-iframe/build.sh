#!/bin/sh
set -e

corepack pnpm install --frozen-lockfile
corepack pnpm build
