#!/bin/zsh
pipeline_project_dir="${0:A:h}"
python3 -u "$pipeline_project_dir/tools/enviar_aws.py" "$@"
pipeline_exit_code=$?
if [ "$pipeline_exit_code" -ne 0 ]; then
    printf '\nPressione Enter para fechar.'
    read -r pipeline_acknowledgement
fi
exit "$pipeline_exit_code"
