# %%
import pandas as pd 
import geopandas as gpd 
import numpy as np
from shapely import wkt 
from shapely.geometry import Point, Polygon, MultiPolygon

import matplotlib.pyplot as plt

from tqdm import tqdm 
from glob import glob 

import os
import sys 

# %%
PROJ_CRS = 'EPSG:2263'
# increasing the distance acts as a smoothing kernal, as more points get to 'count' the traffic from an image
MAX_DISTANCE=150
FOV =  180 # Field of view in degrees
DEBUG_SAMPLE=False
LOCAL_PATH=''

# %%
# load nyc ntas 
nyc_ntas = gpd.read_file("../data/nynta2020_24b/nynta2020.shp").to_crs(PROJ_CRS)

# %%
nyc_ntas.NTAName.values

# %%
DoCs = ["2023-08-11", "2023-08-12", "2023-08-13", "2023-08-14", "2023-08-17", "2023-08-18", "2023-08-20", "2023-08-21", "2023-08-22", "2023-08-23", "2023-08-24", "2023-08-28", "2023-08-29", "2023-08-30", "2023-08-31"]
traffic = [] 
for day in DoCs: 
    print(f"Processing {day}")
    day_data = pd.read_csv(f"{LOCAL_PATH}/{day}/detections.csv", engine='pyarrow', index_col=0)[['0','1','2']].fillna(0)
    day_md = pd.read_csv(f"{LOCAL_PATH}/{day}/md.csv", engine='pyarrow', index_col=0)
    traffic.append(day_md.merge(day_data, left_on='frame_id', right_index=True))
    
traffic = pd.concat(traffic)

# print summary statistics of traffic 
print(traffic.describe())



# %%
# take a random sample 
if DEBUG_SAMPLE:
    traffic = traffic.sample(100000).reset_index(drop=True)

# %%
traffic = gpd.GeoDataFrame(traffic, geometry=gpd.points_from_xy(traffic['gps_info.longitude'], traffic['gps_info.latitude']), crs='EPSG:4326').to_crs(PROJ_CRS)

# %%
traffic['camera_heading'].describe()  

# %%
# load nyc sidewalk graph 
nyc_sidewalks = pd.read_csv("../data/segmentized_nyc_sidewalks.csv", engine='pyarrow')
nyc_sidewalks = gpd.GeoDataFrame(nyc_sidewalks, geometry=nyc_sidewalks['geometry'].apply(wkt.loads), crs='EPSG:2263')

# %%
# set first column to be named 'point index' 
nyc_sidewalks.columns = ['point_index'] + list(nyc_sidewalks.columns[1:])

# %%
nyc_sidewalks.shape_area = nyc_sidewalks.shape_area.astype(float)
nyc_sidewalks.shape_leng = nyc_sidewalks.shape_leng.astype(float)
nyc_sidewalks['shape_width'] = nyc_sidewalks.shape_area / nyc_sidewalks.shape_leng

# %%
len(nyc_sidewalks['source_id'].unique())

# %%
traffic['direction'].value_counts()

# %%


# %%
# map direction column (NORTH_WEST, etc.) to a degree value 0-360 in new column 
dir_mapping = {
    'NORTH': 0,
    'NORTH_EAST': 45,
    'EAST': 90,
    'SOUTH_EAST': 135,
    'SOUTH': 180,
    'SOUTH_WEST': 225,
    'WEST': 270,
    'NORTH_WEST': 315
}
traffic['snapped_heading'] = traffic['direction'].map(dir_mapping)
traffic['snapped_heading'].describe()

# %%
# drop na rows on snapped_heading 
traffic = traffic.dropna(subset=['snapped_heading'])

# %%
# if original geometry column exists, swap it in and drop it 
if 'original_geometry' in traffic.columns:
    traffic['geometry'] = traffic['original_geometry']
    traffic = traffic.drop(columns=['original_geometry'])

def create_semicircle(point, heading, distance):
    # Convert the heading to radians
    heading_rad = np.deg2rad(heading)

    # Generate points for the semicircle
    num_points = 10  # Number of points to approximate the semicircle
    angles = np.linspace(heading_rad - np.pi / 2, heading_rad + np.pi / 2, num_points)
    
    semicircle_points = [point]
    for angle in angles:
        semicircle_points.append(Point(point.x + distance * np.cos(angle),
                                       point.y + distance * np.sin(angle)))
    semicircle_points.append(point)  # Close the semicircle

    # Create the semicircle polygon
    semicircle = Polygon(semicircle_points)

    return semicircle


# Ensure both GeoDataFrames are in the same CRS
if traffic.crs != nyc_sidewalks.crs:
    nyc_sidewalks = nyc_sidewalks.to_crs(traffic.crs)

# Store the original geometry
traffic['original_geometry'] = traffic['geometry']

# Create semicircle geometries
traffic['geometry'] = traffic.apply(lambda row: create_semicircle(row['geometry'], row['camera_heading'], MAX_DISTANCE), axis=1)

# %%
gpd.GeoSeries(traffic['geometry'].iloc[1]).plot()

# %%
# visualize the geometry on a map 
fig, ax = plt.subplots(figsize=(20, 20))

# crop to greenpoint 
neighborhood = nyc_ntas[nyc_ntas.NTAName == 'Greenpoint']

# crop plots to neighborhood 
gpd.clip(nyc_sidewalks, neighborhood).plot(ax=ax, color='black', alpha=0.5, markersize=0.5)
gpd.clip(traffic, neighborhood).plot(ax=ax, color='red', alpha=0.5)

# %%

# Perform a spatial join to find all points in nyc_sidewalks within the cone
traffic = gpd.sjoin(traffic, nyc_sidewalks, how='inner', predicate='intersects')

# Restore the original geometry
traffic['geometry'] = traffic['original_geometry']

# Drop the original_geometry column if no longer needed
traffic = traffic.drop(columns=['original_geometry'])

# Print the number of joined points
print(len(traffic))

# %%
# get average traffic per sidewalk
# traffic is in 0, 1, 2 columns 
avg_traffic_by_sidewalk = traffic.groupby('point_index')[['0','1','2']].mean()
avg_traffic_by_sidewalk = nyc_sidewalks.merge(avg_traffic_by_sidewalk, left_on='point_index', right_index=True, how='left').fillna(0)

# %%
# created a 'crowdedness' metric that multiplies the number of people by a multiplier of relative to average sidewalk width
avg_traffic_by_sidewalk['rta_width'] = avg_traffic_by_sidewalk['shape_width'] / avg_traffic_by_sidewalk['shape_width'].mean()
avg_traffic_by_sidewalk['crowdedness'] = avg_traffic_by_sidewalk['0'] / avg_traffic_by_sidewalk['shape_width']

# %%
pd.options.display.float_format = '{:.4f}'.format
avg_traffic_by_sidewalk['crowdedness'].describe([0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95, 0.975, 0.99, 0.999])

# %%
traffic['timestamp'] = pd.to_datetime(traffic['captured_at'], unit='ms').dt.tz_localize('UTC').dt.tz_convert('America/New_York')
traffic['timestamp'].describe()

# %%
# Add crowdedness by hour 
traffic['hour'] = traffic['timestamp'].dt.hour


all_point_indexes = pd.DataFrame({'point_index': nyc_sidewalks['point_index'].unique()})


for hour in range(24): 
    # Group by source_id and hour to calculate the mean
    hourly_traffic = traffic[traffic['hour'] == hour].groupby('point_index')['0'].mean().reset_index().fillna(0)

    
    # Merge with all_source_ids to ensure all source_ids are present
    hourly_traffic = all_point_indexes.merge(hourly_traffic, on='point_index', how='left').fillna(0)


    # Add the column to avg_traffic_by_sidewalk
    avg_traffic_by_sidewalk[f'crowdedness_{hour}'] = hourly_traffic['0']
    avg_traffic_by_sidewalk[f'crowdedness_{hour}'] = avg_traffic_by_sidewalk[f'crowdedness_{hour}'].fillna(0)
    avg_traffic_by_sidewalk[f'crowdedness_{hour}'] = avg_traffic_by_sidewalk[f'crowdedness_{hour}'] / avg_traffic_by_sidewalk['shape_width']


    # only clip if percent nonzero is > 0.01 
    if avg_traffic_by_sidewalk[avg_traffic_by_sidewalk[f'crowdedness_{hour}'] > 0].shape[0] / avg_traffic_by_sidewalk.shape[0] > 0.01: 
        avg_traffic_by_sidewalk[f'crowdedness_{hour}'] = avg_traffic_by_sidewalk[f'crowdedness_{hour}'].clip(lower=avg_traffic_by_sidewalk[f'crowdedness_{hour}'].quantile(0.001), upper=avg_traffic_by_sidewalk[f'crowdedness_{hour}'].quantile(0.999))


# %%
# set max columns to display in pandas 
pd.set_option('display.max_columns', 500)
avg_traffic_by_sidewalk.describe([0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95, 0.975, 0.99])

# %%
print(avg_traffic_by_sidewalk['crowdedness'].describe([0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95, 0.975, 0.99]))

# %%
# clamp crowdedness to 1st and 99th percentile
avg_traffic_by_sidewalk['crowdedness'] = avg_traffic_by_sidewalk['crowdedness'].clip(lower=avg_traffic_by_sidewalk['crowdedness'].quantile(0.01), upper=avg_traffic_by_sidewalk['crowdedness'].quantile(0.99))

# %%
# for crowdedness_1 to crowdedness_23, plot a map frame of a gif and save 
# Calculate global min and max for all crowdedness columns
crowdedness_columns = [f'crowdedness_{hour}' for hour in range(24)]

# get vmin and vmax at clipped 1st and 99th percentile
vmin = avg_traffic_by_sidewalk[crowdedness_columns].quantile(0.01).min()
vmax = avg_traffic_by_sidewalk[crowdedness_columns].quantile(0.99).max()
norm = plt.Normalize(vmin, vmax)


# %%
print(vmin, vmax)

# %%
fig, ax = plt.subplots(figsize=(20, 20))
nyc_sidewalks.plot(ax=ax, color='grey', alpha=0.5, markersize=0.5)

# Plot the data with a colormap and get the ScalarMappable
plot = avg_traffic_by_sidewalk.plot(ax=ax, column='crowdedness', legend=False, markersize=0.5, cmap='viridis', alpha=0.5)

# Create a colorbar
cbar = plt.colorbar(plot.get_children()[0], ax=ax, orientation='horizontal', shrink=0.5, pad=0.025)
cbar.set_label("# Pedestrians per Foot of Sidewalk Width", fontsize=24)
cbar.ax.tick_params(labelsize=20)

ax.set_axis_off()

plt.savefig("../figures/crowdedness.png", dpi=300, bbox_inches="tight", pad_inches=0.025)

plt.close()



# how many points are missing crowdedness data
zero_crowdedness_count = (avg_traffic_by_sidewalk['crowdedness'] == 0).sum()
total_points = avg_traffic_by_sidewalk.shape[0]
zero_crowdedness_percentage = zero_crowdedness_count / total_points * 100

print(f"Rows with 0 crowdedness data: {zero_crowdedness_count} points, {zero_crowdedness_percentage:.2f}% of all points")

# %%
# write average traffic to disk 
avg_traffic_by_sidewalk.to_csv(f"../data/avg_traffic_by_sidewalk_august.csv")

# %%



