from pathlib import Path
import re
from collections import namedtuple
import random
import pandas as pd
from PIL import Image
import numpy as np
import torch
from torch.autograd import Variable
from torch.utils.data import Dataset
import torchvision.transforms as transforms

# Relative local directories
ROOT_DIR = Path.cwd()
DATA_DIR = ROOT_DIR / '..' / 'data'


def load_image_ndarray(image_path):
    """
    Returns a single datapoint at a given index
    :param image_path: (string) filepath for image
    :return: (ndarray) 3-channel image
    """
    # Load image using PIL
    image = Image.open(image_path)
    # Strip out 4th channel
    img_array = np.array(image)
    img_stripped = img_array[:, :, :3]
    return img_stripped


def plot_image(image):
    """
    Plots an image using PIL
    :param image: (PILImage) image to plot
    """
    import matplotlib.pyplot as plt
    plt.figure()
    plt.tight_layout()
    plt.imshow(image)
    plt.axis('off')
    plt.show()


def ndimage_to_variable(nd_image, **kwargs):
    """
    Converts an incoming np image into a PyTorch variable
    :param nd_image: (ndarray) image
    :return: (Variable) image tensor
    """
    # Scales to image size and converts to tensor
    loader = transforms.Compose([
        transforms.ToPILImage(),
        transforms.Resize(kwargs['imsize']),
        transforms.ToTensor(),
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])])
    # Strip out 4th channel
    img_stripped = nd_image[:, :, :3]
    if kwargs['use_gpu']:
        img = Variable(loader(img_stripped).cuda())
    else:
        img = Variable(loader(img_stripped))
    # Fake a batch dimension
    img = img.unsqueeze(0)
    return img


def _extract_target_from_gazefilename(imagepath, filename_regex='(\d.\d+)_(\d.\d+).png'):
    """
    Extract the label from the image path name
    :imagepath: (Path) image path (contains target)
    :filename_regex: (string) regex used to extract gaze data from filename
    :return: tuple(int, int) gaze target
    """
    m = re.search(filename_regex, imagepath.name)
    gaze_x = float(m.group(1))
    gaze_y = float(m.group(2))
    return gaze_x, gaze_y


def _bounding_box(image_path, default_color=(255, 0, 255), plot=False):
    """
    Uses PIL to get bounding box of face/shoulders for a synthetic image
    :param image_path: (Path) image path
    :param default_color: (3-tuple) default or "green-screen" color
    :param plot: (bool) plot image for debug purposes
    :return: left, upper, right, lower coordinates of bbox
    """
    # Need the image in ndarray form to get bounding box
    image = load_image_ndarray(image_path)
    # Change default color pixels to zero
    image[np.where((image == default_color).all(axis=2))] = [0, 0, 0]
    # use PIL function to get bounding box over non-zero
    pil_image = Image.fromarray(image)
    left, upper, right, lower = pil_image.getbbox()
    # Plot bounding box image
    if plot:
        from PIL import ImageDraw
        draw = ImageDraw.Draw(pil_image)
        draw.rectangle([left, upper, right, lower])
        plot_image(pil_image)
    return left, upper, right, lower


class BackgroundMask(object):
    """Transform adds a background image to a given fake image sample dict"""

    def __init__(self, background_dataset=None, default_color=(255, 0, 255)):
        self.background_dataset = DATA_DIR / background_dataset
        self.default_color = default_color

    def _random_background(self, imsize):
        """
        Pick a random background image from dataset
        :param imsize: (tuple) height and width of image
        :return: (ndarray) background image
        """
        # Choose random background image
        random_image_path = random.choice(self.background_dataset.glob('*.png'))
        background_img = Image.open(str(random_image_path))
        # Use size from original image to crop background
        background_loader = transforms.Compose([
            transforms.RandomCrop(imsize)
        ])
        img = background_loader(background_img)
        return np.array(img)  # Return as ndarray

    def __call__(self, sample):
        image = sample['image']
        h, w = image.shape[:2]
        background_image = self._random_background((h, w))
        # Use default color to mask array and combine
        mask = np.where((image == self.default_color).all(axis=2))
        background_image[mask] = image[mask]
        # Put the image back into the sample dictionary
        sample['image'] = background_image
        return sample


class GazeDataset(Dataset):
    """Gaze Dataset Class. Inherits from PyTorch's Dataset class."""

    def __init__(self, datasets, phase, dataset_type='real', transform=None):
        """
        :dataset_name: (string) comma separated list of datasets
        :phase: (string) either 'test' or 'train'
        :transform: (optional callable) transform to be applied to image
        """
        self.datasets = datasets.split(',')
        self.phase = phase
        self.dataset_type = dataset_type
        if self.dataset_type == 'real':
            self.columns = ['imagepath', 'gaze_x', 'gaze_y']
        elif self.dataset_type == 'fake':
            self.columns = ['imagepath', 'gaze_x', 'gaze_y', 'bb_left', 'bb_upper', 'bb_right', 'bb_lower']
        self.transform = transform
        self.dataset = self._load_dataset()

    def _load_dataset(self):
        """
        Loads dataset into a pandas dataframe
        :return: (pd) dataset with (filename, gaze_x, gaze_y) as header columns
        """
        image_list = []
        # Load and combine all the individual datasets
        for dataset in self.datasets:
            image_list_size = len(image_list)
            data_path = Path(dataset) / self.phase
            full_path = DATA_DIR / data_path
            image_list.extend(full_path.glob('*.png'))
            dataset_size = len(image_list) - image_list_size
            print('Found %s images in dataset %s' % (dataset_size, str(data_path)))
        print('Combined dataset contains %s images.' % len(image_list))
        # Create new pandas dataframe
        df = pd.DataFrame(index=list(range(len(image_list))), columns=self.columns)
        # Add all images in dataset folder into dataframe
        for i, imagepath in enumerate(image_list):
            gaze_x, gaze_y = _extract_target_from_gazefilename(imagepath)
            if self.dataset_type == 'fake':
                left, upper, right, lower = _bounding_box(imagepath)  # , plot=True) # DEBUG
                df.loc[i] = [imagepath, gaze_x, gaze_y, left, upper, right, lower]
            elif self.dataset_type == 'real':
                df.loc[i] = [imagepath, gaze_x, gaze_y]
        return df

    def __len__(self):
        return len(self.dataset)

    def __getitem__(self, idx):
        """
        Gets a single item of dataset based on index
        :param idx: (int) index of datapoint to retreive
        :return sample: (dict) image, gaze target
        """
        image = load_image_ndarray(self.dataset.iloc[idx, 0])
        # Apply transform if necessary
        if self.transform:
            image = self.transform(image)
        # Put info into a dictionary
        sample = {'image': image}
        for i, col_name in enumerate(self.columns, 1):
            sample[col_name] = self.dataset.iloc[i, 1]
        return sample

    def plot_samples(self, num_images=3):
        """
        Plots random sample images
        :num_images: (int) number of images to plot per dataset
        """
        import matplotlib.pyplot as plt
        plt.figure()
        for i in range(num_images):
            sample_idx = random.randint(0, self.__len__())
            sample = self.__getitem__(sample_idx)
            # Use sublots to plot all of them
            ax = plt.subplot(1, num_images, i + 1)
            plt.tight_layout()
            plt.imshow(sample['image'])
            ax.set_title('Image %s: (%.2f, %.2f)' % (sample_idx,
                                                     sample['gaze_x'],
                                                     sample['gaze_y']))
            ax.axis('off')
        plt.show()


# Custom named tuple is used for retreiving DataLoader objects
GazeDataLoader = namedtuple('GazeDataLoader', ['dataloader', 'dataset'])


def gaze_dataloader(**kwargs):
    """
    Creates dataloaders to be used for training a gaze model
    :return: (dict of namedtuples) contains DataLoaders, GazeDataset
    """

    # Data transform define transformation applied to each image before being fed into model
    data_transforms = {
        'train': transforms.Compose([
            # transforms.ToPILImage(),
            transforms.Resize(kwargs['imsize']),
            transforms.ColorJitter(brightness=0.2, contrast=0.2, saturation=0.2, hue=0.2),
            transforms.ToTensor(),
            transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
        ]),
        'test': transforms.Compose([
            # transforms.ToPILImage(),
            transforms.Resize(kwargs['imsize']),
            transforms.ToTensor(),
            transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
        ])
    }

    # Populate the return dictionary with datasets and dataloaders
    return_dict = {}
    for phase in data_transforms.keys():
        if phase == 'test' and kwargs.get('test', False):
            dataset = 'test_dataset'  # Use explicit test dataset
        else:
            dataset = kwargs['datasets']
        dataset = GazeDataset(dataset, phase, transform=data_transforms[phase])
        dataloader = torch.utils.data.DataLoader(dataset,
                                                 batch_size=kwargs['batch_size'],
                                                 shuffle=True,
                                                 num_workers=kwargs['num_workers'])
        # Push into our custom namedtuple
        return_dict[phase] = GazeDataLoader(dataloader, dataset)
    return return_dict
