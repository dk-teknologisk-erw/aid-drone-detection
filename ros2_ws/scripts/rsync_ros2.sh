rsync_ros2() {
    local user="$1"
    local remote_ip="$2"
    local rel_path="$3"
    local dry_flag="${4:-n}"   # default to 'n' (dry-run)

    if [[ -z "$user" || -z "$remote_ip" || -z "$rel_path" ]]; then
        echo "Usage: rsync_ros2 <user> <remote_ip> <relative_path> [y|n]"
        echo "  <user>           Remote username"
        echo "  <remote_ip>      Remote host IP or hostname"
        echo "  <relative_path>  Path under /home/<user>/ros2_ws on remote"
        echo "  [y|n]            Dry-run (-n) ? (default: n)"
        return 1
    fi

    local rsync_dry=""
    if [[ "$dry_flag" == "n" || "$dry_flag" == "N" ]]; then
        rsync_dry="-n"
        echo "Running in DRY-RUN mode (no changes will be made)."
    fi

    rsync -avz $rsync_dry \
        --exclude-from="/home/erik/.rsyncignore" \
        "$rel_path" \
        "${user}@${remote_ip}:/home/${user}/ros2_ws/"
}
