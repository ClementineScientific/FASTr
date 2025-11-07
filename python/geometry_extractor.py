"""
Simplified geometry extractor for OpenFAST input files
Designed to run in browser via Pyodide with minimal dependencies
"""

import json
import re
from typing import Dict, List, Any, Optional


class GeometryExtractor:
    """Extract geometric data from OpenFAST input files"""
    
    def __init__(self):
        self.geometry = {
            'config': {},
            'blades': {},
            'tower': {},
            'hub': {},
            'errors': [],
            'warnings': [],
            'filesRead': []
        }
        self.files = {}  # Store uploaded files: {filename: content}
    
    def add_file(self, filename: str, content: str):
        """Add a file to the virtual filesystem"""
        self.files[filename] = content
    
    def get_file(self, filepath: str):
        """Get file by exact path or normalized basename"""
        import os
        
        # Try exact match first
        if filepath in self.files:
            return self.files[filepath]
        
        # Try basename match
        basename = os.path.basename(filepath)
        if basename in self.files:
            return self.files[basename]
        
        # Try matching any uploaded file with same basename
        for uploaded_name, content in self.files.items():
            if os.path.basename(uploaded_name) == basename:
                return content
        
        return None
        
    def read_value(self, line: str, value_type=str):
        """Extract value from OpenFAST input line (value followed by comment)"""
        try:
            # Split by common delimiters and take first part
            parts = re.split(r'[!\-]', line.strip())
            if not parts[0].strip():
                return None
            
            value = parts[0].strip().split()[0]
            
            # Remove quotes
            value = value.strip('"').strip("'")
            
            if value_type == float:
                return float(value)
            elif value_type == int:
                return int(value)
            elif value_type == bool:
                return value.lower() in ('true', 't', 'yes', '1')
            else:
                return value
        except:
            return None
    
    def parse_main_file(self, content: str) -> bool:
        """Parse the main .fst file"""
        lines = content.split('\n')
        try:
            # Find input file references
            found_ed = False
            found_aero = False
            
            for i, line in enumerate(lines):
                # Skip full comment lines
                line_stripped = line.strip()
                if not line_stripped or line_stripped.startswith('!') or line_stripped.startswith('---'):
                    continue
                
                # Check if line contains file reference keywords
                # Keywords can be anywhere in the line (value or comment)
                if 'EDFile' in line or 'ElastoDyn input file' in line:
                    ed_file = self.read_value(line, str)
                    ed_content = self.get_file(ed_file) if ed_file else None
                    
                    if ed_content:
                        found_ed = True
                        self.parse_elastodyn_file(ed_content)
                    elif ed_file:
                        self.geometry['warnings'].append(f"EDFile '{ed_file}' not found")
                        
                elif 'AeroFile' in line or 'AeroDyn input file' in line:
                    aero_file = self.read_value(line, str)
                    aero_content = self.get_file(aero_file) if aero_file else None
                    
                    if aero_content:
                        found_aero = True
                        self.parse_aerodyn_file(aero_content)
            
            if not found_ed:
                self.geometry['warnings'].append("No ElastoDyn file reference found or file not uploaded")
            
            self.geometry['filesRead'].append('main.fst')
            return True
        except Exception as e:
            self.geometry['errors'].append(f"Error parsing main file: {str(e)}")
            return False
    
    def parse_elastodyn_file(self, content: str) -> bool:
        """Parse ElastoDyn input file for structural geometry"""
        lines = content.split('\n')
        try:
            # Track values we need to combine
            tower_ht = None
            twr2shft = None
            tower_bs_ht = None
            
            i = 0
            while i < len(lines):
                line = lines[i].strip()
                
                # Number of blades
                if 'NumBl' in line:
                    self.geometry['config']['numBlades'] = self.read_value(line, int)
                
                # Rotor geometry
                elif 'TipRad' in line:
                    tip_rad = self.read_value(line, float)
                    if tip_rad:
                        self.geometry['config']['rotorDiameter'] = tip_rad * 2
                        self.geometry['blades']['length'] = tip_rad
                        
                elif 'HubRad' in line:
                    self.geometry['hub']['radius'] = self.read_value(line, float)
                    
                elif 'PreCone' in line:
                    self.geometry['blades']['precone'] = self.read_value(line, float)
                    
                elif 'OverHang' in line:
                    self.geometry['hub']['overhang'] = self.read_value(line, float)
                    
                elif 'ShftTilt' in line:
                    self.geometry['hub']['shaftTilt'] = self.read_value(line, float)
                
                # Tower geometry
                elif 'TowerHt' in line and 'TowerBsHt' not in line:
                    tower_ht = self.read_value(line, float)
                    if tower_ht:
                        self.geometry['tower']['height'] = tower_ht
                
                elif 'TowerBsHt' in line:
                    tower_bs_ht = self.read_value(line, float)
                    if tower_bs_ht is not None:
                        self.geometry['tower']['baseElevation'] = tower_bs_ht
                
                elif 'Twr2Shft' in line:
                    twr2shft = self.read_value(line, float)
                
                # Blade file reference
                elif 'BldFile' in line:
                    blade_file = self.read_value(line, str)
                    blade_content = self.get_file(blade_file) if blade_file else None
                    if blade_content:
                        self.parse_blade_file(blade_content)
                
                # Tower file reference
                elif 'TwrFile' in line:
                    tower_file = self.read_value(line, str)
                    tower_content = self.get_file(tower_file) if tower_file else None
                    if tower_content:
                        self.parse_tower_file(tower_content)
                
                i += 1
            
            # Calculate hub height: TowerHt + Twr2Shft
            if tower_ht is not None:
                hub_height = tower_ht
                if twr2shft is not None:
                    hub_height += twr2shft
                self.geometry['config']['hubHeight'] = hub_height
                self.geometry['warnings'].append(f"Hub height calculated: {hub_height}m (TowerHt={tower_ht} + Twr2Shft={twr2shft or 0})")
            
            self.geometry['filesRead'].append('ElastoDyn')
            return True
        except Exception as e:
            self.geometry['errors'].append(f"Error parsing ElastoDyn file: {str(e)}")
            return False
    
    def parse_blade_file(self, content: str) -> bool:
        """Parse blade distributed properties file for stick figure geometry"""
        lines = content.split('\n')
        try:
            # Find the distributed properties table
            # Format: BlFract  PitchAxis  StrcTwst  BMassDen  FlpStff  EdgStff
            stations = []
            in_table = False
            
            for i, line in enumerate(lines):
                # Look for column headers
                line_upper = line.upper()
                if 'BLFRACT' in line_upper:
                    in_table = True
                    continue
                    
                if in_table and line.strip() and not line.strip().startswith('!') and not line.strip().startswith('---'):
                    # Remove inline comments
                    if '!' in line:
                        line = line.split('!')[0]
                    
                    parts = line.split()
                    if len(parts) >= 3:
                        try:
                            # ElastoDyn blade format:
                            # BlFract (0-1), PitchAxis (-), StrcTwst (deg), BMassDen, FlpStff, EdgStff
                            station = {
                                'spanFraction': float(parts[0]),  # Fraction along blade (0-1)
                                'pitchAxis': float(parts[1]),     # Pitch axis location
                                'twist': float(parts[2]),         # Structural twist (degrees)
                            }
                            stations.append(station)
                        except ValueError:
                            pass  # Skip malformed lines
            
            if stations:
                self.geometry['blades']['stations'] = stations
                self.geometry['filesRead'].append('Blade properties')
                self.geometry['warnings'].append(f"Parsed {len(stations)} blade stations")
            
            return True
        except Exception as e:
            self.geometry['warnings'].append(f"Could not parse blade properties: {str(e)}")
            return False
    
    def parse_tower_file(self, content: str) -> bool:
        """Parse tower distributed properties file for stick figure geometry"""
        lines = content.split('\n')
        try:
            # Find the distributed properties table
            # Format: HtFract  TMassDen  TwFAStif  TwSSStif
            stations = []
            in_table = False
            
            for i, line in enumerate(lines):
                line_upper = line.upper()
                
                # Check for end of table (next section header)
                if in_table and line.strip().startswith('---'):
                    break
                
                if 'HTFRACT' in line_upper:
                    in_table = True
                    continue
                    
                if in_table and line.strip() and not line.strip().startswith('!') and not line.strip().startswith('---'):
                    # Remove inline comments
                    if '!' in line:
                        line = line.split('!')[0]
                    
                    parts = line.split()
                    if len(parts) >= 1:
                        try:
                            # ElastoDyn tower format:
                            # HtFract (0-1), TMassDen, TwFAStif, TwSSStif
                            # For stick figure, we only need the height fraction
                            height_frac = float(parts[0])
                            
                            # Sanity check: HtFract should be between 0 and 1
                            if 0 <= height_frac <= 1:
                                station = {
                                    'heightFraction': height_frac,
                                }
                                stations.append(station)
                            else:
                                # Hit invalid data, likely end of table
                                break
                        except ValueError:
                            # Hit non-numeric data, likely end of table
                            if in_table:
                                break
            
            if stations:
                self.geometry['tower']['stations'] = stations
                self.geometry['filesRead'].append('Tower properties')
                self.geometry['warnings'].append(f"Parsed {len(stations)} tower stations")
            
            return True
        except Exception as e:
            self.geometry['warnings'].append(f"Could not parse tower properties: {str(e)}")
            return False
    
    def parse_aerodyn_file(self, content: str) -> bool:
        """Parse AeroDyn input file for airfoil data"""
        # Simplified - just mark as read
        self.geometry['filesRead'].append('AeroDyn')
        return True
    
    def extract_geometry(self) -> str:
        """Extract geometry from all loaded files and return as JSON"""
        # Find and parse main file
        main_file = None
        for filename, content in self.files.items():
            if filename.endswith('.fst'):
                main_file = content
                break
        
        if not main_file:
            self.geometry['errors'].append("No .fst file found")
            return json.dumps({'success': False, 'geometry': self.geometry})
        
        success = self.parse_main_file(main_file)
        
        return json.dumps({
            'success': success and len(self.geometry['errors']) == 0,
            'geometry': self.geometry
        })


# Function to be called from JavaScript
def extract_openfast_geometry(files_dict: Dict[str, str]) -> str:
    """
    Main entry point for JavaScript
    
    Args:
        files_dict: Dictionary of {filename: file_content}
    
    Returns:
        JSON string with geometry data
    """
    extractor = GeometryExtractor()
    
    for filename, content in files_dict.items():
        extractor.add_file(filename, content)
    
    return extractor.extract_geometry()
