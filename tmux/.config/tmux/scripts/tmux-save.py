#!/usr/bin/env python3

import os
import shlex
import subprocess
import sys


def check_running():
    cmd = "tmux ls"
    retcode = subprocess.call(shlex.split(cmd))
    if retcode != 0:
        exit(1)


def start_server(socket, output):
    if socket != "default":
        print("shopt -s expand_aliases", file=output)
        print(f'alias tmux="tmux -L {socket}"', file=output)
    print("tmux start-server", file=output)


def get_pane_base_index():
    cmd = "tmux show -gv pane-base-index"
    try:
        output = subprocess.check_output(shlex.split(cmd)).decode().strip()
    except subprocess.CalledProcessError:
        return 0
    try:
        return int(output)
    except ValueError:
        return 0


def create_sessions(file_output):
    server_started = False
    cmd = 'tmux list-sessions -F "#S #{session_attached} #{socket_path} #W p=#{pane_current_path} #{session_grouped} g=#{session_group}"'
    output = subprocess.check_output(shlex.split(cmd)).decode()
    stdout_mode = file_output == "-"
    base_dir = file_output if not stdout_mode else None
    grouped_sessions = set()
    for line in output.splitlines():
        try:
            session, attached, socket_path, window, path, grouped, group = line.split()
            socket = socket_path[1 + socket_path.rfind("/") :]
            path = path[1 + path.find("=") :]
            grouped = bool(int(grouped))
            group = group[1 + group.find("=") :]
            if stdout_mode:
                if not server_started:
                    file_output = sys.stdout
                    start_server(socket, file_output)
                    server_started = True
                if grouped:
                    print_info = (
                        "-P -F 'new session #{session_name} group #{session_group}'"
                    )
                    print(
                        f"tmux new-session -d {print_info} -s {session} -t {group}",
                        file=file_output,
                    )
                else:
                    print_info = "-P -F 'new session #{session_name}'"
                    path_info = f" -c '{path}'" if path else ""
                    print(
                        f"tmux has-session -t {session} || tmux new-session -d {print_info}{path_info} -n {window} -s {session}",
                        file=file_output,
                    )
                if group not in grouped_sessions:
                    create_windows(session, file_output)
                    grouped_sessions.add(group)
            else:
                linked_windows = dict()
                session_output = os.path.join(base_dir or "", session)
                descriptor = os.open(
                    path=session_output,
                    flags=os.O_WRONLY | os.O_TRUNC | os.O_CREAT,
                    mode=0o700,
                )
                session_file = open(descriptor, "w")
                print("#!/usr/bin/env bash", file=session_file)
                start_server(socket, session_file)
                if grouped:
                    print_info = (
                        "-P -F 'new session #{session_name} group #{session_group}'"
                    )
                    print(
                        f"tmux has-session -t {group} || tmux new-session -d -s {group}",
                        file=session_file,
                    )
                    print(
                        f"tmux new-session -d {print_info} -s {session} -t {group}",
                        file=session_file,
                    )
                    if group in grouped_sessions:
                        session_file.close()
                        continue
                    grouped_sessions.add(group)
                else:
                    print_info = "-P -F 'new session #{session_name}'"
                    path_info = f" -c '{path}'" if path else ""
                    print(
                        f"tmux has-session -t {session} || tmux new-session -d {print_info}{path_info} -n {window} -s {session}",
                        file=session_file,
                    )
                create_windows(session, session_file, linked_windows)
                print("if [ -z \"$TMUX\" ]; then", file=session_file)
                print(f"  tmux attach-session -t {session}", file=session_file)
                print("else", file=session_file)
                print(f"  tmux switch-client -t {session}", file=session_file)
                print("fi", file=session_file)
                session_file.close()
        except ValueError:
            print("line", line)
            raise
    return file_output


def create_windows(session, file_output, linked_windows=None):
    if linked_windows is None:
        linked_windows = dict()
    fmt = '-F "#{window_id} #W #{window_index} #{window_active} #{window_linked} #{window_layout} p=#{pane_current_path} #{window_panes}"'
    cmd = f"tmux list-windows -t {session} {fmt}"
    output = subprocess.check_output(shlex.split(cmd)).decode()
    for line in output.splitlines():
        window_id, window, index, active, linked, layout, path, pane_count = line.split()
        linked = bool(int(linked))
        active = int(active)
        pane_count = int(pane_count)
        path = path[1 + path.find("=") :]
        if linked:
            if window_id in linked_windows:
                source = linked_windows[window_id]
                destination = f"{session}:{index}"
                print(
                    f"tmux link-window -s {source} -t {destination}", file=file_output
                )
                continue
            linked_windows[window_id] = f"{session}:{index}"
        print_info = "-P -F 'new window #S:#W at #{window_index}'"
        session_window = f"{session}:{index}"
        path_info = f" -c '{path}'" if path else ""
        print(
            f"tmux new-window -d -k {print_info}{path_info} -n {window} -t {session_window}",
            file=file_output,
        )
        if pane_count > 1:
            split_panes(session_window, file_output)
        print(f"tmux select-layout -t {session_window} '{layout}'", file=file_output)
        if active != 0:
            print(f"tmux select-window -t {session_window}", file=file_output)


def link_histfiles():
    cmd = 'tmux list-panes -a -F "#{session_name} #{window_index} #{pane_index} #{pane_tty}"'
    output = subprocess.check_output(shlex.split(cmd)).decode()
    for line in output.splitlines():
        session, window, index, tty = line.split()
        path = os.path.expanduser("~/.bash_history.d/")
        src = path + tty[1 + tty.rfind("/") :]
        dst = path + f"{session}-{window}-{index}"
        if os.path.exists(src):
            if os.stat(src).st_size > 0:
                if os.path.exists(dst):
                    os.unlink(dst)
                os.link(src, dst)


def split_panes(session_window, file_output):
    base_index = get_pane_base_index()
    fmt = '-F "#P #{pane_active} p=#{pane_current_path}"'
    cmd = f"tmux list-panes -t {session_window} {fmt}"
    output = subprocess.check_output(shlex.split(cmd)).decode()
    for line in output.splitlines():
        try:
            index, active, path = line.split()
            index = int(index)
            active = int(active)
            path = path[1 + path.find("=") :]
            if index != base_index:
                print_info = "-P -F 'split window #S:#{window_index}'"
                path_info = f" -c '{path}'" if path else ""
                target_index = index - 1
                print(
                    f"tmux split-window {print_info} -t {session_window}.{target_index}{path_info}",
                    file=file_output,
                )
            if active != 0:
                print(f"tmux select-pane -t {session_window}.{index}", file=file_output)
        except ValueError:
            print("line", line)
            raise


def generate_script(file_output):
    file_output = create_sessions(file_output)
    link_histfiles()
    return file_output


def main():
    args = sys.argv[1:]
    file_output = os.path.expanduser("~/.tmux_sessions") if not args else args[0]
    check_running()
    file_output = generate_script(file_output)
    if hasattr(file_output, "close"):
        file_output.close()


if __name__ == "__main__":
    main()
