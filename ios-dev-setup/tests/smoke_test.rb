# frozen_string_literal: true

require "fileutils"
require "minitest/autorun"
require "open3"
require "tmpdir"

class IosDevSetupScriptsSmokeTest < Minitest::Test
  SKILL_ROOT = File.expand_path("..", __dir__)
  SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
  GIT_ENV = {
    "GIT_AUTHOR_NAME" => "iOS Skill Smoke",
    "GIT_AUTHOR_EMAIL" => "ios-skill-smoke@example.test",
    "GIT_COMMITTER_NAME" => "iOS Skill Smoke",
    "GIT_COMMITTER_EMAIL" => "ios-skill-smoke@example.test",
    "GIT_ALLOW_PROTOCOL" => "file"
  }.freeze

  def script(name)
    File.join(SKILL_ROOT, "scripts", name)
  end

  def capture(*command, env: {}, chdir: nil)
    options = chdir ? { chdir: chdir } : {}
    Open3.capture3({ "LC_ALL" => "C" }.merge(env), *command, **options)
  end

  def write_executable(directory, name, body)
    path = File.join(directory, name)
    File.write(path, body)
    FileUtils.chmod(0o755, path)
  end

  def git!(*arguments, chdir:)
    out, err, status = capture("git", *arguments, env: GIT_ENV, chdir: chdir)
    raise "git #{arguments.join(' ')} failed: #{err}" unless status.success?

    out
  end

  def init_repo(path, marker)
    FileUtils.mkdir_p(path)
    git!("init", "-q", "-b", "main", chdir: path)
    File.write(File.join(path, "README.md"), "#{marker}\n")
    git!("add", "README.md", chdir: path)
    git!("commit", "-q", "-m", "initialize #{marker}", chdir: path)
  end

  def add_initialized_submodule(repo, submodule_repo, relative_path)
    git!("-c", "protocol.file.allow=always", "submodule", "add", "-q", submodule_repo, relative_path, chdir: repo)
    git!("commit", "-q", "-m", "add #{relative_path}", chdir: repo)
  end

  def add_uninitialized_gitlink(repo, submodule_repo, relative_path)
    oid = git!("rev-parse", "HEAD", chdir: submodule_repo).strip
    File.write(
      File.join(repo, ".gitmodules"),
      "[submodule \"missing\"]\n\tpath = #{relative_path}\n\turl = #{submodule_repo}\n"
    )
    git!("add", ".gitmodules", chdir: repo)
    git!("update-index", "--add", "--cacheinfo", "160000,#{oid},#{relative_path}", chdir: repo)
    git!("commit", "-q", "-m", "add uninitialized #{relative_path}", chdir: repo)
  end

  def test_install_xcode_help_and_dry_run
    _out, err, status = capture("bash", script("install_xcode.sh"), "--help")
    assert_predicate status, :success?
    assert_includes err, "Usage:"

    Dir.mktmpdir("ios-install-xcode-smoke") do |bin|
      write_executable(bin, "brew", "#!/usr/bin/env bash\nexit 0\n")
      write_executable(bin, "sw_vers", "#!/usr/bin/env bash\necho 99.0\n")
      out, err, status = capture(
        "bash", script("install_xcode.sh"), "--runtime", "--dry-run",
        env: { "PATH" => "#{bin}:#{SYSTEM_PATH}" }
      )
      text = out + err
      assert_predicate status, :success?
      assert_includes text, "+ xcodes install --latest --select"
      assert_includes text, "+ sudo xcodebuild -runFirstLaunch"
      assert_includes text, "+ xcodebuild -downloadPlatform iOS"
    end
  end

  def test_scan_gitlab_access_help_and_missing_workspace
    out, _err, status = capture("ruby", script("scan_gitlab_access.rb"), "--help")
    assert_predicate status, :success?
    assert_includes out, "Usage:"

    Dir.mktmpdir("ios-access-scan-smoke") do |workspace|
      _out, err, status = capture("ruby", script("scan_gitlab_access.rb"), workspace)
      refute_predicate status, :success?
      assert_equal 2, status.exitstatus
      assert_includes err, "No GitLab dependencies found"
    end

    Dir.mktmpdir("ios-access-redaction-smoke") do |root|
      bin = File.join(root, "bin")
      workspace = File.join(root, "workspace")
      FileUtils.mkdir_p([bin, File.join(workspace, "g0-ios")])
      File.write(
        File.join(workspace, "g0-ios", "Podfile"),
        "pod 'PrivateRepo', :git => 'https://code.example.test/group/no-git-suffix'\n" \
        "source 'https://cdn.example.test/cocoapods/specs'\n"
      )
      write_executable(bin, "git", <<~'SH')
        #!/usr/bin/env bash
        if [[ "${1:-}" == "-C" && "${3:-}" == "remote" ]]; then
          printf 'https://%s@gitlab.example.test/group/repo.git?private_token=%s#fragment\n' \
            "$SMOKE_URL_USERINFO" "$SMOKE_QUERY_VALUE"
          exit 0
        fi
        if [[ "${1:-}" == "ls-remote" ]]; then
          echo "fatal: $SMOKE_ERROR_SECRET; unable to access '$2': repository not found" >&2
          exit 128
        fi
        exit 1
      SH
      userinfo = %w[scan credential].join(":")
      query_value = %w[scan query credential].join("-")
      error_secret = %w[server credential].join("-")
      out, err, status = capture(
        "ruby", script("scan_gitlab_access.rb"), "--check", workspace,
        env: {
          "PATH" => "#{bin}:#{SYSTEM_PATH}",
          "SMOKE_URL_USERINFO" => userinfo,
          "SMOKE_QUERY_VALUE" => query_value,
          "SMOKE_ERROR_SECRET" => error_secret
        }
      )
      text = out + err
      refute_predicate status, :success?
      assert_includes text, "check: https://gitlab.example.test/group/repo.git"
      assert_includes text, "check: https://code.example.test/group/no-git-suffix"
      refute_includes text, "cdn.example.test"
      refute_includes text, userinfo
      refute_includes text, query_value
      refute_includes text, error_secret
      refute_includes text, "private_token="
      assert_includes text, "authentication or repository permission failed"
    end
  end

  def test_preflight_help_and_origin_redaction
    out, _err, status = capture("bash", script("preflight_ios_env.sh"), "--help")
    assert_predicate status, :success?
    assert_includes out, "Usage:"

    Dir.mktmpdir("ios-preflight-smoke") do |root|
      bin = File.join(root, "bin")
      workspace = File.join(root, "workspace")
      FileUtils.mkdir_p(bin)
      %w[g0-ios g0-flutter-module smartdevicecoresdk-ios].each do |repo|
        FileUtils.mkdir_p(File.join(workspace, repo, ".git"))
      end

      write_executable(bin, "git", <<~'SH')
        #!/usr/bin/env bash
        case "$*" in
          *"branch --show-current"*) echo "feat/smoke" ;;
          *"status --short"*) exit 0 ;;
          *"remote get-url origin"*)
            printf 'https://%s@gitlab.example.test/group/repo.git?private_token=%s#fragment\n' \
              "$SMOKE_URL_USERINFO" "$SMOKE_QUERY_VALUE"
            ;;
          *) exit 1 ;;
        esac
      SH
      write_executable(bin, "xcode-select", "#!/usr/bin/env bash\necho /Applications/Xcode.app/Contents/Developer\n")
      write_executable(bin, "xcodebuild", "#!/usr/bin/env bash\necho Xcode 99.0\n")
      write_executable(bin, "security", "#!/usr/bin/env bash\nexit 0\n")

      userinfo = %w[smoke credential].join(":")
      query_value = %w[query credential].join("-")
      out, err, status = capture(
        "bash", script("preflight_ios_env.sh"), workspace,
        env: {
          "PATH" => "#{bin}:#{SYSTEM_PATH}",
          "SMOKE_URL_USERINFO" => userinfo,
          "SMOKE_QUERY_VALUE" => query_value
        }
      )
      text = out + err
      refute_predicate status, :success?
      assert_includes text, "origin=https://gitlab.example.test/group/repo.git"
      refute_includes text, userinfo
      refute_includes text, query_value
      refute_includes text, "private_token="
    end
  end

  def test_create_worktrees_help_and_argument_gate
    _out, err, status = capture("bash", script("create-ios-worktrees.sh"), "--help")
    assert_predicate status, :success?
    assert_includes err, "Usage:"

    _out, err, status = capture("bash", script("create-ios-worktrees.sh"))
    refute_predicate status, :success?
    assert_equal 2, status.exitstatus
    assert_includes err, "Usage:"
  end

  def test_create_worktrees_success_with_submodule_reuse_and_secure_signing_copy
    Dir.mktmpdir("ios-worktrees-success-smoke") do |root|
      ijk = File.join(root, "ijk")
      live_sdk = File.join(root, "live-sdk")
      ios = File.join(root, "g0-ios-main")
      flutter = File.join(root, "g0-flutter-main")
      sdk = File.join(root, "sdk-main")
      init_repo(ijk, "ijk")
      init_repo(live_sdk, "live-sdk")
      init_repo(ios, "ios")
      init_repo(flutter, "flutter")
      init_repo(sdk, "sdk")

      ios_submodule = "MediaCodec/MediaCodec/ijk"
      sdk_submodule = "SmartDeviceCoreSDK/Source/SmartLiveSDK/SmartWebRTC/A4xLiveSDK"
      add_initialized_submodule(ios, ijk, ios_submodule)
      add_initialized_submodule(sdk, live_sdk, sdk_submodule)

      signing_file = File.join(ios, "AddxAi/AppConfig/fastlane_sign.plist")
      FileUtils.mkdir_p(File.dirname(signing_file))
      File.write(signing_file, "opaque signing fixture\n")
      FileUtils.chmod(0o644, signing_file)

      group = File.join(root, "group")
      out, err, status = capture(
        "bash", script("create-ios-worktrees.sh"),
        ios, flutter, sdk, "main", "main", "main", "feat/smoke-success", group,
        env: GIT_ENV
      )
      assert_predicate status, :success?, out + err

      %w[g0-ios g0-flutter-module smartdevicecoresdk-ios].each do |repo_name|
        branch = git!("symbolic-ref", "--short", "HEAD", chdir: File.join(group, repo_name)).strip
        assert_equal "feat/smoke-success", branch
      end

      assert_equal(
        git!("rev-parse", "HEAD", chdir: ijk).strip,
        git!("rev-parse", "HEAD", chdir: File.join(group, "g0-ios", ios_submodule)).strip
      )
      assert_equal(
        git!("rev-parse", "HEAD", chdir: live_sdk).strip,
        git!("rev-parse", "HEAD", chdir: File.join(group, "smartdevicecoresdk-ios", sdk_submodule)).strip
      )

      copied_signing_file = File.join(group, "g0-ios", "AddxAi/AppConfig/fastlane_sign.plist")
      assert_equal "opaque signing fixture\n", File.read(copied_signing_file)
      assert_equal 0o600, File.stat(copied_signing_file).mode & 0o777
      assert_includes out, "Verified g0-ios sibling paths"
    end
  end

  def test_create_worktrees_blocks_missing_submodule_object_store
    Dir.mktmpdir("ios-worktrees-submodule-gate-smoke") do |root|
      submodule = File.join(root, "submodule")
      ios = File.join(root, "g0-ios-main")
      flutter = File.join(root, "g0-flutter-main")
      sdk = File.join(root, "sdk-main")
      init_repo(submodule, "submodule")
      init_repo(ios, "ios")
      init_repo(flutter, "flutter")
      init_repo(sdk, "sdk")
      git!("switch", "-q", "-c", "target-with-submodule", chdir: ios)
      add_uninitialized_gitlink(ios, submodule, "MediaCodec/MediaCodec/ijk")
      git!("switch", "-q", "main", chdir: ios)

      group = File.join(root, "group")
      out, err, status = capture(
        "bash", script("create-ios-worktrees.sh"),
        ios, flutter, sdk, "target-with-submodule", "main", "main", "feat/smoke-block", group,
        env: GIT_ENV
      )
      refute_predicate status, :success?
      assert_includes out + err, "must initialize submodule"
      refute File.exist?(File.join(group, "g0-ios"))
    end
  end
end
