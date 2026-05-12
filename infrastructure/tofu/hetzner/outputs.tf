output "vm_ipv4" {
  # Public IPv4 address of the fabric VM.
  # Used by Ansible inventory (Task 24) and referenced in bootstrap-checklist.md
  # for the initial tofu apply verification step.
  description = "Public IPv4 address of the fabric VM."
  value       = hcloud_server.fabric.ipv4_address
}

output "vm_ipv6" {
  # Public IPv6 address of the fabric VM.
  # Some Hetzner locations may not assign an IPv6 address; try() returns null
  # gracefully in that case rather than erroring out.
  description = "Public IPv6 address of the fabric VM (null if IPv6 is unavailable in the region)."
  value       = try(hcloud_server.fabric.ipv6_address, null)
}

output "volume_device" {
  # Linux block device path for the attached data volume (e.g. /dev/sdb).
  # Referenced in Ansible base role (Task 02) to write the /etc/fstab entry
  # and format/mount the volume on first boot.
  description = "Linux block device path for the attached data volume (e.g. /dev/sdb)."
  value       = hcloud_volume.fabric_data.linux_device
}

output "server_name" {
  # Stable server name — used as the Ansible inventory hostname.
  description = "Hetzner server name as assigned in the API."
  value       = hcloud_server.fabric.name
}

output "firewall_id" {
  # Hetzner firewall resource ID — useful for audit verification that the
  # correct firewall is attached after apply.
  description = "ID of the Hetzner firewall resource attached to the fabric VM."
  value       = hcloud_firewall.fabric.id
}
