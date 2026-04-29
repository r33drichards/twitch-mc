package mcp.filesystem

default allow = false

# LOCAL skills dir — full read/write access
local_prefix := "/home/ubuntu/.local/share/sleet1213/plugin/skills/"

# REPO skills dir — read-only access
repo_prefix := "/home/ubuntu/sleet1213/ted-plugin/skills/"

# coords.md — read/write access (shared coordinate reference)
coords_path := "/home/ubuntu/mca-src/coords.md"

# Read-only operations
readonly_ops := {"readFile", "readdir", "stat", "exists"}

# Read-write operations
readwrite_ops := {"writeFile", "appendFile", "mkdir", "rm", "rename", "copyFile"}

# Allow read-only ops on both LOCAL and REPO skill dirs
allow if {
    readonly_ops[input.operation]
    startswith(input.path, local_prefix)
}

allow if {
    readonly_ops[input.operation]
    startswith(input.path, repo_prefix)
}

# Allow read-write ops ONLY on LOCAL skill dir
allow if {
    readwrite_ops[input.operation]
    startswith(input.path, local_prefix)
    check_destination
}

# coords.md — read and write access (exact path only)
allow if {
    readonly_ops[input.operation]
    input.path == coords_path
}

allow if {
    input.operation == "writeFile"
    input.path == coords_path
}

allow if {
    input.operation == "appendFile"
    input.path == coords_path
}

# For rename/copyFile, destination must also be in LOCAL skill dir
check_destination if {
    not input.destination
}

check_destination if {
    input.destination
    startswith(input.destination, local_prefix)
}
