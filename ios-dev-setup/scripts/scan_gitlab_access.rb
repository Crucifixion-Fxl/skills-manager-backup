#!/usr/bin/env ruby
# frozen_string_literal: true

require "open3"
require "optparse"
require "pathname"
require "set"
require "timeout"
require "uri"

options = { check: false }
OptionParser.new do |parser|
  parser.banner = "Usage: #{File.basename($PROGRAM_NAME)} [--check] [workspace]"
  parser.on("--check", "Verify each repository with read-only git ls-remote") { options[:check] = true }
end.parse!

workspace = Pathname.new(ARGV.shift || Dir.pwd).expand_path
workspace = workspace.parent if %w[g0-ios g0-flutter-module smartdevicecoresdk-ios].include?(workspace.basename.to_s)

repo_files = {
  "g0-ios" => %w[.gitmodules Podfile Podfile.lock Gemfile Gemfile.lock fastlane/Pluginfile],
  "g0-flutter-module" => %w[.gitmodules pubspec.yaml pubspec.lock],
  "smartdevicecoresdk-ios" => %w[.gitmodules Podfile Podfile.lock Gemfile Package.swift Cartfile]
}

url_pattern = %r{(?:git@[^:\s]+:[^\s"'\\]+|ssh://git@[^/\s]+/[^\s"'\\]+|https://[^\s"'\\]+)}i
dependencies = {}

def redact_url_credentials(value)
  value.to_s
       .gsub(%r{(?<scheme>[a-z][a-z0-9+.-]*://)[^/@\s]+@}i, '\\k<scheme>')
       .sub(/[?#].*\z/, "")
end

def repository_host(url)
  case url
  when %r{\Agit@([^:]+):}i, %r{\Assh://(?:[^@/]+@)?([^/:]+)}i
    Regexp.last_match(1).downcase
  when %r{\Ahttps://}i
    URI.parse(redact_url_credentials(url)).host&.downcase
  end
rescue URI::InvalidURIError
  nil
end

def git_dependency_context?(relative, line, nearby_lines)
  return true if relative == ".gitmodules"

  inline_git = /(?:^\s*git\s*:|:git\s*=>|\bgit\s*[:=]|\b(?:git|github)\s+["']|\.package\s*\([^)]*\burl\s*:)/i
  return true if line.match?(inline_git)
  return false unless %w[pubspec.yaml pubspec.lock].include?(File.basename(relative))

  nearby_lines.join.match?(/(?:\bsource\s*:\s*git\b|^\s*git\s*:)/im)
end

def https_repository_candidate?(url, git_hosts, git_context)
  return true unless url.match?(%r{\Ahttps://}i)

  clean = redact_url_credentials(url).sub(/[\)`\],;]+\z/, "")
  uri = URI.parse(clean)
  host = uri.host&.downcase
  uri.path.end_with?(".git") || git_hosts.include?(host) || host&.include?("gitlab") || git_context
rescue URI::InvalidURIError
  false
end

normalize = lambda do |url|
  clean = redact_url_credentials(url).sub(/[\)`\],;]+\z/, "").sub(/\.git\z/, "")
  clean = clean.sub(%r{\Ahttps://}i, "")
  clean = clean.sub(%r{\Assh://git@}i, "")
  clean = clean.sub(%r{\Agit@([^:]+):}i, '\\1/')
  clean
end

add = lambda do |url, source|
  key = normalize.call(url)
  return if key.empty?

  record = (dependencies[key] ||= { sources: [], url: url })
  record[:sources] << source unless record[:sources].include?(source)
  record[:url] = url if url.start_with?("git@", "ssh://")
end

repositories = repo_files.each_with_object([]) do |(repo_name, relative_files), result|
  repo = workspace / repo_name
  result << [repo_name, repo, relative_files] if repo.directory?
end

git_hosts = Set.new
repositories.each do |repo_name, repo, _relative_files|
  origin, status = Open3.capture2("git", "-C", repo.to_s, "remote", "get-url", "origin")
  next unless status.success?

  origin = origin.strip
  add.call(origin, "#{repo_name}:origin")
  host = repository_host(origin)
  git_hosts << host if host
end

repositories.each do |repo_name, repo, relative_files|
  relative_files.each do |relative|
    file = repo / relative
    next unless file.file?

    lines = file.readlines
    lines.each_with_index do |line, index|
      first_context_line = [index - 6, 0].max
      last_context_line = [index + 6, lines.length - 1].min
      nearby_lines = lines[first_context_line..last_context_line]
      git_context = git_dependency_context?(relative, line, nearby_lines)
      line.scan(url_pattern) do |url|
        next unless https_repository_candidate?(url, git_hosts, git_context)

        add.call(url, "#{repo_name}/#{relative}:#{index + 1}")
      end
    end
  end
end

if dependencies.empty?
  warn "No GitLab dependencies found under #{workspace}. Expected sibling g0 repositories."
  exit 2
end

def check_repo(url)
  env = {
    "GIT_TERMINAL_PROMPT" => "0",
    "GIT_SSH_COMMAND" => "ssh -o BatchMode=yes -o ConnectTimeout=8"
  }
  stdin, stdout, stderr, waiter = Open3.popen3(env, "git", "ls-remote", url, "HEAD")
  stdin.close
  begin
    output = Timeout.timeout(20) { stdout.read + stderr.read }
    status = Timeout.timeout(2) { waiter.value }
    [status.success?, output.strip]
  rescue Timeout::Error
    Process.kill("TERM", waiter.pid) rescue nil
    [false, "timeout"]
  ensure
    stdout.close rescue nil
    stderr.close rescue nil
  end
end

def summarize_error(detail)
  return "DNS resolution failed" if detail.match?(/Could not resolve/i)
  return "network connection timed out or is unreachable" if detail.match?(/Connection timed out|Operation timed out|No route to host/i)
  return "SSH host key verification failed" if detail.match?(/Host key verification failed/i)
  return "authentication or repository permission failed" if detail.match?(/Permission denied|repository not found|authentication/i)

  "git ls-remote failed; server output hidden"
end

puts "Workspace: #{workspace}"
puts "Found #{dependencies.length} unique Git repositories/sources."
failures = 0

dependencies.keys.sort.each do |key|
  record = dependencies.fetch(key)
  target = record[:url]
  if options[:check]
    success, detail = check_repo(target)
    state = success ? "OK" : "FAIL"
    failures += 1 unless success
    puts "[#{state}] #{key}"
    puts "  check: #{redact_url_credentials(target)}"
    puts "  error: #{summarize_error(detail)}" unless success || detail.empty?
  else
    puts "[LIST] #{key}"
  end
  puts "  from: #{record[:sources].join(', ')}"
end

exit 1 if failures.positive?
