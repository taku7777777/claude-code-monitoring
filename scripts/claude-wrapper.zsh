# claude-code-monitoring: workspace 判定ラッパ (requirements.md 5.2)
# 導入: cat scripts/claude-wrapper.zsh >> ~/.zshrc  (その後 source ~/.zshrc)
# CWD から親へ遡って .claude/settings.local.json → .claude/settings.json の順に
# OTEL_RESOURCE_ATTRIBUTES を探し、export してから claude 本体を起動する。
claude() {
  emulate -L zsh
  setopt local_options no_xtrace no_verbose no_print_exit_value
  if (( $+commands[jq] )); then
    local dir=$PWD attrs= candidate=
    while [[ -n $dir && $dir != / ]]; do
      for candidate in $dir/.claude/settings.local.json $dir/.claude/settings.json; do
        if [[ -f $candidate ]]; then
          attrs=$(jq -r '.env.OTEL_RESOURCE_ATTRIBUTES // empty' $candidate 2>/dev/null)
          [[ -n $attrs ]] && break 2
        fi
      done
      dir=${dir:h}
    done
    if [[ -n $attrs ]]; then
      OTEL_RESOURCE_ATTRIBUTES=$attrs command claude "$@"
      return
    fi
  fi
  command claude "$@"
}
