#!/bin/zsh

DRY_RUN=false

owner=pymc-devs
repo=pytensor
issue_number=1124
title="Speed up test times :rocket:"
workflow=Tests
latest_id=$(gh run list --branch main --limit 1 --workflow $workflow --status success --json databaseId,startedAt,updatedAt --jq '
. | map({
  databaseId: .databaseId,
  startedAt: .startedAt,
  updatedAt: .updatedAt,
  minutes:  (((.updatedAt | fromdate) - (.startedAt | fromdate)) / 60)
} | select(.minutes > 10)) 
| .[0].databaseId
')
jobs=$(gh api /repos/$owner/$repo/actions/runs/$latest_id/jobs --jq '
.jobs 
| map({name: .name, run_id: .run_id, id: .id, started_at: .started_at, completed_at: .completed_at})
')

# Skip 3.10, float32, and Benchmark tests
function skip_job() {
    name=$1
    if [[ $name == *"py3.10"* ]]; then
        return 0
    fi

    if [[ $name == *"float32 1"* ]]; then
        return 0
    fi

    if [[ $name == *"Benchmark"* ]]; then
        return 0
    fi

    return 1
}

# Remove common prefix from the name
function remove_prefix() {
    name=$1
    echo $name | sed -e 's/^ubuntu-latest test py3.13 numpy>=2.0 : fast-compile 0 : float32 0 : //'
}

function human_readable_time() {
    started_at=$1
    completed_at=$2

    start_seconds=$(date -d "$started_at" +%s)
    end_seconds=$(date -d "$completed_at" +%s)

    seconds=$(($end_seconds - $start_seconds))

    if [ $seconds -lt 60 ]; then
        echo "$seconds seconds"
    else
        echo "$(date -u -d @$seconds +'%-M minutes %-S seconds')"
    fi
}

all_times=""
echo "$jobs" | jq -c '.[]' | while read -r job; do
    id=$(echo $job | jq -r '.id')
    name=$(echo $job | jq -r '.name')
    run_id=$(echo $job | jq -r '.run_id')
    started_at=$(echo $job | jq -r '.started_at')
    completed_at=$(echo $job | jq -r '.completed_at')

    if skip_job $name; then
        echo "Skipping $name"
        continue
    fi

    echo "Processing job: $name (ID: $id, Run ID: $run_id)"

    # Seeing a bit more stabilty with the API rather than the CLI
    # https://docs.github.com/en/rest/actions/workflow-jobs?apiVersion=2022-11-28#download-job-logs-for-a-workflow-run
    times=$(gh api /repos/$owner/$repo/actions/jobs/$id/logs | python extract-slow-tests.py)
    # times=$(gh run view --job $id --log | python extract-slow-tests.py)

    if [ -z "$times" ]; then
        # Some of the jobs are non-test jobs, so we skip them
        echo "No tests found for '$name', skipping"
        continue
    fi

    echo $times

    human_readable=$(human_readable_time $started_at $completed_at)
    name=$(remove_prefix $name)

    top="<details><summary>($human_readable) $name</summary>\n\n\n\`\`\`"
    bottom="\`\`\`\n\n</details>"

    formatted_times="$top\n$times\n$bottom"

    if [ -n "$all_times" ]; then
        all_times="$all_times\n$formatted_times"
    else
        all_times="$formatted_times"
    fi
done

if [ -z "$all_times" ]; then
    echo "No slow tests found, exiting"
    exit 1
fi

run_date=$(date +"%Y-%m-%d")
body=$(cat << EOF
If you are motivated to help speed up some tests, we would appreciate it!

Here are some of the slowest test times:

$all_times

You can find more information on how to contribute [here](https://pytensor.readthedocs.io/en/latest/dev_start_guide.html)

Automatically generated by [GitHub Action](https://github.com/pymc-devs/pytensor/blob/main/.github/workflows/slow-tests-issue.yml)
Latest run date: $run_date 
Run logs: [$latest_id](https://github.com/pymc-devs/pytensor/actions/runs/$latest_id)
EOF
)

if [ "$DRY_RUN" = true ]; then
    echo "Dry run, not updating issue"
    echo $body
    exit
fi
echo $body | gh issue edit $issue_number --body-file - --title "$title"
echo "Updated issue $issue_number with all times"
