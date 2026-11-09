# The demo shell: the usual PATH, a short prompt, no history written.
[ -f ~/.bashrc ] && . ~/.bashrc >/dev/null 2>&1
unset PROMPT_COMMAND
HISTFILE=/dev/null
PS1='\[\e[38;2;153;153;153m\]\w\[\e[0m\] \[\e[38;2;232;232;232m\]$\[\e[0m\] '
cd ~
clear
