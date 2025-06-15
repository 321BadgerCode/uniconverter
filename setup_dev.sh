#!/bin/bash
git config commit.template ./.git_commit_template.txt
mkdir -p ./.git/hooks
cp ./.git_hooks/* ./.git/hooks/
chmod +x ./.git/hooks/*