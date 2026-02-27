#!/usr/bin/env python3
"""
Codex Code Review Script
Analyzes code changes for best practices, bugs, and security issues.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path


class CodexReviewer:
    """Code reviewer for common issues"""
    
    # Language-specific patterns
    PATTERNS = {
        'rust': {
            'best_practices': [
                (r'unwrap\(\)', 'Consider using proper error handling instead of unwrap()'),
                (r'println!\s*\(', 'Prefer logging over println! for production code'),
                (r'Vec::new\(\)\.push\(', 'Use Vec::with_capacity() when size is known'),
                (r'\.clone\(\)', 'Avoid unnecessary clones, consider references'),
            ],
            'bugs': [
                (r'panic!', 'Avoid panic! in production code, use Result<T, E>'),
                (r'expect\(', 'Replace expect() with proper error handling'),
                (r'unsafe\s', 'Unsafe block detected - ensure it\'s necessary and safe'),
            ],
            'security': [
                (r'println!\s*\(.*password', 'Don\'t log passwords or sensitive data'),
                (r'dbg!\s*\(.*password', 'Don\'t include passwords in debug output'),
                (r'env!\s*\(.+.\+.\+', 'Be careful with string concatenation in commands'),
            ],
        },
        'go': {
            'best_practices': [
                (r'fmt\.Print\w*\(', 'Use logging instead of fmt.Print'),
                (r'\[\]byte\(string\)', 'Use []byte(string) - more idiomatic'),
                (r'if err != nil \{[^}]+\}', 'Consider wrapping with if err == nil'),
            ],
            'bugs': [
                (r'defer\s+\w+\(\)', 'Check for errors before defer'),
                (r'range\s+\w+\(\)\s+\{', 'Ensure range limit is checked'),
            ],
            'security': [
                (r'fmt\.Sprint.*password', 'Don\'t include passwords in formatted strings'),
                (r'os\.Exec.*\+.*password', 'Don\'t concatenate passwords in commands'),
            ],
        },
    }
    
    def __init__(self, diff_file):
        """Initialize reviewer with diff file"""
        self.diff_file = diff_file
        self.issues = []
        
    def parse_diff(self):
        """Parse diff file to get changed files and hunks"""
        with open(self.diff_file, 'r') as f:
            content = f.read()
        
        # Parse diff format
        files = {}
        current_file = None
        for line in content.split('\n'):
            if line.startswith('diff --git'):
                current_file = line.split(' ')[2].split('/')[-1]
                files[current_file] = []
            elif current_file and line.startswith('@@'):
                hunk = {
                    'file': current_file,
                    'line_start': int(line.split('@@')[1].split(',')[0][1:]),
                    'content': []
                }
                files[current_file].append(hunk)
            elif current_file and files[current_file]:
                files[current_file][-1]['content'].append(line)
        
        return files
    
    def detect_language(self, filename):
        """Detect programming language from file extension"""
        ext = Path(filename).suffix.lower()
        lang_map = {
            '.rs': 'rust',
            '.go': 'go',
            '.py': 'python',
            '.js': 'javascript',
            '.ts': 'typescript',
            '.java': 'java',
        }
        return lang_map.get(ext, 'generic')
    
    def analyze_file(self, filename, hunks):
        """Analyze file hunks for issues"""
        language = self.detect_language(filename)
        patterns = self.PATTERNS.get(language, {})
        
        for hunk in hunks:
            line_num = hunk['line_start']
            for line in hunk['content']:
                # Skip diff markers
                if line.startswith(('+', '-', '@@')):
                    if line.startswith('+'):
                        # Check for issues in added lines
                        for category, pattern_list in patterns.items():
                            for pattern, message in pattern_list:
                                if re.search(pattern, line[1:]):  # Skip the '+' prefix
                                    self.issues.append({
                                        'severity': 'warning',
                                        'category': category,
                                        'file': filename,
                                        'line': line_num,
                                        'message': message,
                                        'code': line[1:].strip()
                                    })
                    line_num += 1
    
    def review(self):
        """Run full review process"""
        if not os.path.exists(self.diff_file):
            print(f"Error: Diff file not found: {self.diff_file}", file=sys.stderr)
            return None
        
        print(f"Analyzing diff: {self.diff_file}")
        
        # Parse diff
        files = self.parse_diff()
        
        # Analyze each file
        for filename, hunks in files.items():
            self.analyze_file(filename, hunks)
        
        # Generate summary
        result = {
            'summary': f'Code review completed with {len(self.issues)} issues found',
            'issues': self.issues,
            'files_reviewed': len(files)
        }
        
        return result
    
    def print_review(self, result):
        """Print review results in human-readable format"""
        print("\n" + "="*60)
        print("🔍 CODEX CODE REVIEW")
        print("="*60)
        print(f"\nFiles reviewed: {result['files_reviewed']}")
        print(f"Issues found: {len(result['issues'])}\n")
        
        # Group by severity
        by_severity = {}
        for issue in result['issues']:
            sev = issue['severity']
            if sev not in by_severity:
                by_severity[sev] = []
            by_severity[sev].append(issue)
        
        for severity in ['error', 'warning', 'info']:
            if severity in by_severity:
                icon = {'error': '❌', 'warning': '⚠️', 'info': 'ℹ️'}[severity]
                print(f"\n{icon} {severity.upper()} ({len(by_severity[severity])})")
                print("-" * 50)
                for issue in by_severity[severity]:
                    print(f"  [{issue['category']}] {issue['file']}:{issue['line']}")
                    print(f"  {issue['message']}")
                    if 'code' in issue:
                        print(f"  Code: {issue['code']}")
                    print()


def main():
    parser = argparse.ArgumentParser(description='Codex Code Review Agent')
    parser.add_argument('--diff', required=True, help='Diff file to review')
    parser.add_argument('--output', help='Output file (JSON format)')
    parser.add_argument('--format', choices=['json', 'text'], default='text',
                       help='Output format')
    
    args = parser.parse_args()
    
    # Run review
    reviewer = CodexReviewer(args.diff)
    result = reviewer.review()
    
    if not result:
        sys.exit(1)
    
    # Output results
    if args.format == 'json':
        output = json.dumps(result, indent=2)
        if args.output:
            with open(args.output, 'w') as f:
                f.write(output)
        else:
            print(output)
    else:
        reviewer.print_review(result)
    
    sys.exit(0)


if __name__ == '__main__':
    main()
