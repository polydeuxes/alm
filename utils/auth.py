# utils/auth.py
import subprocess
import re
import os
from pathlib import Path
import config

def get_profiles():
    """Get list of configured profiles from config.toml"""
    try:
        config_file = Path(config.CONFIG_DIR) / 'config.toml'
        if not config_file.exists():
            return []
        
        profiles = []
        current_profile = None
        with open(config_file) as f:
            for line in f:
                if line.startswith('[profile.'):
                    current_profile = line.strip()[9:-1]
                    profiles.append({
                        'name': current_profile,
                        'country': '',
                        'auth_file': ''
                    })
                elif current_profile and '=' in line:
                    key, value = line.strip().split('=', 1)
                    key = key.strip()
                    value = value.strip().strip('"')
                    if key == 'country_code':
                        profiles[-1]['country'] = value
                    elif key == 'auth_file':
                        profiles[-1]['auth_file'] = value
        
        return profiles
    except Exception as e:
        config.logger.error(f"Error reading profiles: {e}")
        return []

def handle_quickstart(profile_name, country_code, pre_amazon):
    """Handle the quickstart initialization process"""
    try:
        inputs = f"{profile_name}\n{country_code}\n\nn\ny\n{pre_amazon}\ny\n"
        
        process = subprocess.Popen(
            'audible quickstart',
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE,
            shell=True,
            text=True
        )
        
        process.stdin.write(inputs)
        process.stdin.flush()
        
        output = ""
        while True:
            line = process.stdout.readline()
            if not line or "Please insert the copied url" in line:
                break
            output += line
            config.logger.info(f"Process output: {line.strip()}")
        
        url_match = re.search(r'(https://www\.amazon\.com/ap/signin\S+)', output)
        if url_match:
            return {
                'success': True,
                'auth_url': url_match.group(1),
                'instructions': output,
                'process': process
            }
            
        return {
            'success': False,
            'error': 'Could not find authentication URL'
        }
        
    except Exception as e:
        logger.error(f"Quickstart failed: {e}")
        return {
            'success': False,
            'error': str(e)
        }

def handle_additional_profile(profile_name, country_code):
    """Handle adding an additional profile"""
    try:
        config.logger.info(f"Starting additional profile setup: {profile_name} ({country_code})")
        auth_file = f"{profile_name}.json"
        
        # Command using the provided profile name
        inputs = f"{auth_file}\nNone\nNone\n{country_code}\n"
        
        process = subprocess.Popen(
            f'audible -P {profile_name} manage auth-file add --external-login',
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE,
            shell=True,
            text=True,
            cwd=config.CONFIG_DIR  # Ensure we're in the right directory
        )

        # Send initial inputs
        config.logger.info(f"Sending initial inputs: {inputs}")
        process.stdin.write(inputs)
        process.stdin.flush()

        # Read output and look for URL
        output = ""
        while True:
            line = process.stdout.readline()
            config.logger.info(f"Process output: {line.strip()}")
            if not line:
                break
            output += line
            if "https://www.amazon.com/ap/signin" in line:
                config.logger.info("Found authentication URL")
                return {
                    'success': True,
                    'auth_file': auth_file,
                    'instructions': output,
                    'process': process
                }

        config.logger.error(f"No authentication URL found in output: {output}")
        return {
            'success': False,
            'error': 'Could not get authentication URL',
            'debug_output': output
        }

    except Exception as e:
        config.logger.error(f"Additional profile setup failed: {e}")
        return {
            'success': False,
            'error': str(e),
            'debug_output': str(e)
        }
