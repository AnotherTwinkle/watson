while true; do
    read -r -e -p "> " line
    [[ "$line" == "exit" ]] && break
    [[ -z "$line" ]] && continue

    history -s "$line"
    history -w "$HISTFILE"

    eval "python3.12 main.py $line"
done