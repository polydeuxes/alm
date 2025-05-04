# utils/common.py
import subprocess
import config
import time

def run_command(command, input_data=None, timeout=300):
    """Execute a shell command with improved logging and error handling"""
    try:
        # Handle ffmpeg/ffprobe commands differently
        is_media_cmd = isinstance(command, str) and ('ffmpeg' in command or 'ffprobe' in command)
        # Split command if it's a string and not a media command
        if isinstance(command, str) and not is_media_cmd:
            command = command.split()

        use_shell = is_media_cmd

        config.logger.info(f"Running command (DIAGNOSTIC LOGGING): {command} (shell={use_shell})") # Add note

        # Create process with line buffering
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE if input_data else None,
            text=True,
            bufsize=1,
            shell=use_shell
        )

        output_lines = []
        error_lines = []
        loop_count = 0

        # Read output in real-time
        while True:
            loop_count += 1
            stdout_line = None
            stderr_line = None
            poll_status = process.poll()

            config.logger.info(f"Loop #{loop_count}: Polling process status = {poll_status}")

            # Check stdout
            try:
                config.logger.info(f"Loop #{loop_count}: Attempting stdout.readline()")
                stdout_line = process.stdout.readline()
                if stdout_line:
                    line = stdout_line.strip() # Keep stripping for appending, but log raw
                    output_lines.append(line)
                    config.logger.info(f"Loop #{loop_count}: Read stdout RAW: {repr(stdout_line)}")
                    # config.logger.debug(f"Command stdout: {line}") # Original log (optional)
                else:
                     # This is important - indicates readline returned without data (EOF or just no newline)
                     config.logger.info(f"Loop #{loop_count}: stdout.readline() returned empty or EOF.")
            except Exception as stdout_read_exc:
                 config.logger.error(f"Loop #{loop_count}: Exception reading stdout: {stdout_read_exc}", exc_info=True)
                 break # Exit loop on read error

            # Check stderr
            try:
                config.logger.info(f"Loop #{loop_count}: Attempting stderr.readline()")
                stderr_line = process.stderr.readline()
                if stderr_line:
                    line = stderr_line.strip() # Keep stripping for appending, but log raw
                    error_lines.append(line)
                    config.logger.info(f"Loop #{loop_count}: Read stderr RAW: {repr(stderr_line)}") # Log raw line
                    # config.logger.debug(f"Command stderr: {line}") # Original log (optional)
                else:
                    # This is important - indicates readline returned without data (EOF or just no newline)
                    config.logger.info(f"Loop #{loop_count}: stderr.readline() returned empty or EOF.")
            except Exception as stderr_read_exc:
                 config.logger.error(f"Loop #{loop_count}: Exception reading stderr: {stderr_read_exc}", exc_info=True)
                 break # Exit loop on read error

            # Check if process has finished (using the polled status from start of loop)
            if poll_status is not None:
                config.logger.info(f"Loop #{loop_count}: Process polling indicates finished ({poll_status}). Breaking loop.")
                # Maybe read any tiny bit remaining? (Less critical now)
                # for line in process.stdout: output_lines.append(line.strip())
                # for line in process.stderr: error_lines.append(line.strip())
                break

        stdout = '\n'.join(output_lines)
        stderr = '\n'.join(error_lines)

        # Ensure returncode is fetched *after* loop confirms process exit
        final_returncode = process.wait() # Use wait() to be sure after loop exit
        config.logger.info(f"Command final return code: {final_returncode}")

        success = final_returncode == 0
        if not success:
            config.logger.error(f"Command failed with code {final_returncode}")
            config.logger.error(f"Final stderr for failed command: {stderr}") # Log full stderr on failure

        return {
            'success': success,
            'output': stdout,
            'error': stderr,
            'code': final_returncode
        }
    except Exception as e:
        # Keep existing top-level exception handling
        cmd_str = ' '.join(command) if isinstance(command, list) else command
        config.logger.error(f"Exception in run_command for: {cmd_str}", exc_info=True)
        return {
            'success': False,
            'output': '',
            'error': str(e),
            'code': -1
        }
