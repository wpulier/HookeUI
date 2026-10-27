# Task cards (generated)

The goal each participant reads, from `bench.h14_tasks`, as frozen in design §4.1 and §4.3. Values in braces
come from each variant's `params.json` and are filled into that variant's `card.txt` by
`python -m bench.h14_fixtures <out>`. The cards never name a menu, a control or a shortcut (§4.1). A test
checks every rendered card.

| Task | Session | Cap | Start state | Goal (participant wording) |
| --- | --- | --- | --- | --- |
| T0 | 1 | 25 min | fresh macOS user account, neither app installed | Install the app from the file in Downloads and get it ready to find nuclei with Cellpose-SAM. Then use it on the image check.tif in Downloads and keep the result. Say "done" when it is ready and the result is kept. |
| T1 | 1 | 6 min | five-dimensional image open | Show the image with {order} as the displayed axes. Play through time and stop at time point {t}. Go to the channel named "{channel}" and the depth where the brightest of the marked objects is. Tell us its pixel value and its position in micrometres. |
| T2 | 1 | 6 min | app open, nothing loaded | Open the app's own "{sample}" sample data, then open five-d.tif. Show the maximum of {slab} depth planes centred on depth {z}. Give channel "{c1}" the display range {r1} and the colour {cmap1}; give channel "{c2}" the display range {r2} and the colour {cmap2}; hide channel "{c3}". |
| T3 | 1 | 8 min | volume open in 2D | Look at the volume in 3D as a maximum projection, then as an isosurface at threshold {threshold}. Cut it with a plane at depth {depth} so the part above is hidden. Then tell us the label number of the object under the red marker, by pointing at it in the 3D view. |
| T4 | 1 | 8 min | mixed scene open | In this scene: which track is the parent of track {child} after the division? What value does the surface have at the marked vertex? How long is the marked arrow? Then make points from other depths visible at half their size, and show all the data side by side in a grid. |
| T5 | 1 | 8 min | app open, nothing loaded | Open the large dataset big.ome.zarr. Go to the region marked in the overview, at full resolution, and tell us the pixel value at position x {x}, y {y} on depth {z}. |
| T6 | 1 | 8 min | image open | Put a point on the centre of each of the {n} marked cells. Draw a rectangle around region A and a polygon around region B. Then put the polygon behind the rectangle. |
| T7 | 1 | 10 min | labels open over the image | Fix these nucleus labels. Split the merged nucleus marked M into two. Delete the false object marked F. Fill the hole in the nucleus marked H. Then erase the nucleus marked X on purpose, and take that back so it is as it was. |
| T8 | 2 | 10 min | image open, Cellpose-SAM prepared | Find the nuclei in this image with Cellpose-SAM, using a diameter of {diameter} pixels and the "{channel}" channel. Keep the result. |
| T9 | 2 | 8 min | reference labels open beside the image | Measure each nucleus in these labels: its area in square micrometres and its mean intensity. Save the table as a CSV file called measurements.csv in the Results folder. |
| T10 | 2 | 10 min | 3D+t image open | Make a movie with three key views: {k1}; then {k2}; then {k3}. Use {steps} steps between key views. Save it as movie.mp4 in the Results folder. |
| T11 | 2 | 6 min | this session's work, unsaved | Erase the nucleus marked E1. Save all your work, quit the app, open it again, and erase the nucleus marked E2. |
| T12 | 2 | 10 min | image and labels open | Using Python inside the app: add the maximum projection of the image along depth as new data. Change label {old} to {new} in the labels and see it change. Then run the file long_loop.py, stop it before it ends, and check that the variable `answer` you set first still holds {answer}. |
| T13 | 2 | 8 min | remote dataset open | Keep working with the remote dataset. Tell us the pixel value at position x {x}, y {y} on depth {z}. If something goes wrong, get it working again yourself. |
| T14 | 2 | binding manifest | written into the binding manifest | Written into the binding manifest when the held-out extension is chosen (H10). |
| U1 | 2 | 8 min (I4) | labels open; a second editor changes them | Someone else changed these labels while you were editing them. Keep your own work, and decide which version to keep. |
| U2 | 2 | 8 min (I4) | probability map and image open | Measure the mean intensity of each nucleus, using {wrong} as the intensity. If that can't be done, find out why, and measure using the right data instead. |
| U3 | 2 | 8 min (I4) | trained project open | Apply the trained model in this project to new.tif. Then update the model, and go back to the version you started with. |
| U4 | 2 | 8 min (I4) | a batch with failed items | Some items in this batch failed. Run only the failed items again. |
| U5 | 2 | 8 min (I4) | an update is available for an installed package | Update the package {package}. Find out what changed. Then go back to the version you had. |
| U6 | 2 | 8 min (I4) | a project from another computer in Downloads | Open the project in Downloads, which was made on another computer, and look at its data. |
| U7 | 2 | 8 min (I4) | app open | Find out whether "{entry}" is qualified to use, and if it isn't, what it is missing. |
