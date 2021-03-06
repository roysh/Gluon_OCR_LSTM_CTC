import os
import tarfile
import urllib
import sys
import time
import glob
import pickle
import xml.etree.ElementTree as ET
import cv2
import json
import numpy as np
import pandas as pd
import zipfile
import matplotlib.pyplot as plt
import logging

from mxnet.gluon.data import dataset
from mxnet import nd

class IAMDataset(dataset.ArrayDataset):
    """ The IAMDataset provides images of handwritten passages written by multiple
    individuals. The data is available at http://www.fki.inf.unibe.ch

    The passages can be parsed into separate words, lines, or the whole form.
    The dataset should be separated into writer independent training and testing sets.

    Parameters
    ----------
    parse_method: str, Required
        To select the method of parsing the images of the passage
        Available options: [form, line, word]

    credentials: (str, str), Default None 
        Your (username, password) for the IAM dataset. Register at
        http://www.fki.inf.unibe.ch/DBs/iamDB/iLogin/index.php
        By default, IAMDataset will read it from credentials.json
    
    root: str, default: dataset/iamdataset
        Location to save the database

    train: bool, default True
        Whether to load the training or testing set of writers.

    output_data_type: str, default text
        What type of data you want as an output: Text or bounding box.
        Available options are: [text, bb]
     
    output_parse_method: str, default None
        If the bounding box (bb) was selected as an output_data_type, 
        this parameter can select which bb you want to obtain.
        Available options: [form, line, word]
    """
    MAX_IMAGE_SIZE_FORM = (1120, 800)
    def __init__(self, parse_method, credentials=None,
                 root=os.path.join(os.path.dirname(__file__), '..', 'dataset', 'iamdataset'), 
                 train=True, output_data="text",
                 output_parse_method=None):

        _parse_methods = ["form", "line", "word"]
        error_message = "{} is not a possible parsing method: {}".format(
            parse_method, _parse_methods)
        assert parse_method in _parse_methods, error_message
        self._parse_method = parse_method
        url_partial = "http://www.fki.inf.unibe.ch/DBs/iamDB/data/{data_type}/{filename}.tgz"
        if self._parse_method == "form":
            self._data_urls = [url_partial.format(data_type="forms", filename="forms" + a) for a in ["A-D", "E-H", "I-Z"]]
        elif self._parse_method == "line":
            self._data_urls = [url_partial.format(data_type="lines", filename="lines")]
        elif self._parse_method == "word":
            self._data_urls = [url_partial.format(data_type="words", filename="words")]
        self._xml_url = "http://www.fki.inf.unibe.ch/DBs/iamDB/data/xml/xml.tgz"

        if credentials == None:
            if os.path.isfile(os.path.join(os.path.dirname(__file__), '..', 'credentials.json')):
                with open(os.path.join(os.path.dirname(__file__), '..', 'credentials.json')) as f:
                    credentials = json.load(f)
                self._credentials = (credentials["username"], credentials["password"])
            else:
                assert False, "Please enter credentials for the IAM dataset in credentials.json or as arguments"
        else:
            self._credentials = credentials
        
        self._train = train

        _output_data_types = ["text", "bb"]
        error_message = "{} is not a possible output data: {}".format(
            output_data, _output_data_types)
        assert output_data in _output_data_types, error_message
        self._output_data = output_data

        if self._output_data == "bb":
            assert self._parse_method == "form", "Bounding box only works with form."
            _parse_methods = ["form", "line", "word"]
            error_message = "{} is not a possible output parsing method: {}".format(
                output_parse_method, _parse_methods)
            assert output_parse_method in _parse_methods, error_message
            self._output_parse_method = output_parse_method

            self.image_data_file_name = os.path.join(root, "image_data-{}-{}-{}.plk".format(
                self._parse_method, self._output_data, self._output_parse_method))
        else:
            self.image_data_file_name = os.path.join(root, "image_data-{}-{}.plk".format(self._parse_method, self._output_data))

        self._root = root
        if not os.path.isdir(root):
            os.makedirs(root)

        data = self._get_data()
        super(IAMDataset, self).__init__(data)

    @staticmethod
    def _reporthook(count, block_size, total_size):
        ''' Prints a process bar that is compatible with urllib.request.urlretrieve
        '''
        toolbar_width = 40
        percentage = float(count * block_size) / total_size * 100
        # Taken from https://gist.github.com/sibosutd/c1d9ef01d38630750a1d1fe05c367eb8
        sys.stdout.write('\r')
        sys.stdout.write("Completed: [{:{}}] {:>3}%"
                         .format('-' * int(percentage / (100.0 / toolbar_width)),
                                 toolbar_width, int(percentage)))
        sys.stdout.flush()

    def _extract(self, archive_file, archive_type, output_dir):
        ''' Helper function to extract archived files. Available for tar.tgz and zip files
        Parameter
        ---------
        archive_file: str
            Filepath to the archive file
        archive_type: str, options: [tar, zip]
            Select the type of file you want to extract
        output_dir: str
            Location where you want to extract the files to
        '''
        logging.info("Extracting {}".format(archive_file))
        _available_types = ["tar", "zip"]
        error_message = "Archive_type {} is not an available option ({})".format(archive_type, _available_types)
        assert archive_type in _available_types, error_message
        if archive_file == "tar":
            tar = tarfile.open(archive_file, "r:gz")
            tar.extractall(os.path.join(self._root, output_dir))
            tar.close()
        elif archive_type == "zip":
            zip_ref = zipfile.ZipFile(archive_file, 'r')
            zip_ref.extractall(os.path.join(self._root, output_dir))
            zip_ref.close()

    def _download(self, url):
        ''' Helper function to download using the credentials provided
        Parameter
        ---------
        url: str
            The url of the file you want to download.
        '''
        password_mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
        password_mgr.add_password(None, url, self._credentials[0], self._credentials[1])
        handler = urllib.request.HTTPBasicAuthHandler(password_mgr)
        opener = urllib.request.build_opener(handler)
        urllib.request.install_opener(opener)
        opener.open(url)
        filename = os.path.basename(url)
        print("Downloading {}: ".format(filename)) 
        urllib.request.urlretrieve(url, reporthook=self._reporthook,
                                   filename=os.path.join(self._root, filename))[0]
        sys.stdout.write("\n")

    def _download_xml(self):
        ''' Helper function to download and extract the xml of the IAM database
        '''
        archive_file = os.path.join(self._root, os.path.basename(self._xml_url))
        logging.info("Downloding xml from {}".format(self._xml_url))
        if not os.path.isfile(archive_file):
            self._download(self._xml_url)
            self._extract(archive_file, archive_type="tar", output_dir="xml")
            
    def _download_data(self):
        ''' Helper function to download and extract the data of the IAM database
        '''
        for url in self._data_urls:
            logging.info("Downloding data from {}".format(url))
            archive_file = os.path.join(self._root, os.path.basename(url))
            if not os.path.isfile(archive_file):
                self._download(url)
                self._extract(archive_file, archive_type="tar", output_dir=self._parse_method)

    def _download_subject_list(self):
        ''' Helper function to download and extract the subject list of the IAM database
        '''
        url = "http://www.fki.inf.unibe.ch/DBs/iamDB/tasks/largeWriterIndependentTextLineRecognitionTask.zip"
        logging.info("Downloding subject list from {}".format(url))
        archive_file = os.path.join(self._root, os.path.basename(url))
        if not os.path.isfile(archive_file):
            self._download(url)
            self._extract(archive_file, archive_type="zip", output_dir="subject")
        
    def _pre_process_image(self, img_in):
        im = cv2.imread(img_in, cv2.IMREAD_GRAYSCALE)
        # reduce the size of form images so that it can fit in memory.
        if self._parse_method == "form":
            size = im.shape[:2]
            if size[0] > self.MAX_IMAGE_SIZE_FORM[0] or size[1] > self.MAX_IMAGE_SIZE_FORM[1]:
                ratio_w = float(self.MAX_IMAGE_SIZE_FORM[0])/size[0]
                ratio_h = float(self.MAX_IMAGE_SIZE_FORM[1])/size[1]
                ratio = min(ratio_w, ratio_h)
                new_size = tuple([int(x*ratio) for x in size])
                im = cv2.resize(im, (new_size[1], new_size[0]))
                size = im.shape
            
            delta_w = max(0, self.MAX_IMAGE_SIZE_FORM[1] - size[1])
            delta_h = max(0, self.MAX_IMAGE_SIZE_FORM[0] - size[0])
            top, bottom = delta_h//2, delta_h-(delta_h//2)
            left, right = delta_w//2, delta_w-(delta_w//2)
            
            color = im[0][0]
            if color < 230:
                color = 230
            im = cv2.copyMakeBorder(im, top, bottom, left, right, cv2.BORDER_CONSTANT, value=float(color))                
        img_arr = np.asarray(im)
        return img_arr 

    def _get_bb_of_item(self, item, height, width):
        ''' Helper function to find the bounding box (bb) of an item in the xml file.
        All the characters within the item are found and the left-most (min) and right-most (max + length)
        are found. 
        The bounding box emcompasses the left and right most characters in the x and y direction. 

        Parameter
        ---------
        item: xml.etree object for a word/line/form.

        height: int
            Height of the form to calculate percentages of bounding boxes

        width: int
            Width of the form to calculate percentages of bounding boxes

        Returns
        -------
        list
            The bounding box [x, y, w, h] in percentages that encompasses the item.
        '''

        character_list = [a for a in item.iter("cmp")]
        if len(character_list) == 0: # To account for some punctuations that have no words
            return None
        x1 = np.min([int(a.attrib['x']) for a in character_list])
        y1 = np.min([int(a.attrib['y']) for a in character_list])
        x2 = np.max([int(a.attrib['x']) + int(a.attrib['width']) for a in character_list])
        y2 = np.max([int(a.attrib['y']) + int(a.attrib['height'])for a in character_list])

        x1 = float(x1) / width
        x2 = float(x2) / width
        y1 = float(y1) / height
        y2 = float(y2) / height
        bb = [x1, y1, x2 - x1, y2 - y1]
        return bb
    
    def _get_output_data(self, item, height, width):
        ''' Function to obtain the output data (both text and bounding boxes).
        Note that the bounding boxes are rescaled based on the rescale_ratio parameter.

        Parameter
        ---------
        item: xml.etree 
            XML object for a word/line/form.

        height: int
            Height of the form to calculate percentages of bounding boxes

        width: int
            Width of the form to calculate percentages of bounding boxes

        Returns
        -------

        np.array
            A numpy array ouf the output requested (text or the bounding box)
        '''

        output_data = []
        if self._output_data == "text":
            if self._parse_method == "form":
                text = ""
                for line in item.iter('machine-print-line'):
                    text += line.attrib["text"] + "\n"
                output_data.append(text)
            else:
                output_data.append(item.attrib['text'])
        else:
            for item_output in item.iter(self._output_parse_method):
                bb = self._get_bb_of_item(item_output, height, width)
                if bb == None: # Account for words with no letters
                    continue
                output_data.append(bb)
        output_data = np.array(output_data)
        return output_data
            
    def _process_data(self):
        ''' Function that iterates through the downloaded xml file to gather the input images and the
        corresponding output.
        
        Returns
        -------
        pd.DataFrame
            A pandas dataframe that contains the subject, image and output requested.
        '''

        image_data = []
        xml_files = glob.glob(self._root + "/xml/*.xml")
        print("Processing data:")
        logging.info("Processing data")

        for i, xml_file in enumerate(xml_files):
            tree = ET.parse(xml_file)
            root = tree.getroot()
            height, width = int(root.attrib["height"]), int(root.attrib["width"])
            for item in root.iter(self._parse_method):
                if self._parse_method == "form":
                    image_id = item.attrib["id"]
                else:
                    tmp_id = item.attrib["id"]
                    tmp_id_split = tmp_id.split("-")
                    image_id = os.path.join(tmp_id_split[0], tmp_id_split[0] + "-" + tmp_id_split[1], tmp_id)
                image_filename = os.path.join(self._root, self._parse_method, image_id + ".png")
                image_arr = self._pre_process_image(image_filename)
                output_data = self._get_output_data(item, height, width)
                image_data.append([item.attrib["id"], image_arr, output_data])
                self._reporthook(i, 1, len(xml_files))
        image_data = pd.DataFrame(image_data, columns=["subject", "image", "output"])
        image_data.to_pickle(self.image_data_file_name, protocol=2)
        return image_data

    def _process_subjects(self, train_subject_lists = ["trainset", "validationset1", "validationset2"],
                          test_subject_lists = ["testset"]):
        ''' Function to organise the list of subjects to training and testing.
        The IAM dataset provides 4 files: trainset, validationset1, validationset2, and testset each
        with a list of subjects.
        
        Parameters
        ----------
        
        train_subject_lists: [str], default ["trainset", "validationset1", "validationset2"]
            The filenames of the list of subjects to be used for training the model

        test_subject_lists: [str], default ["testset"]
            The filenames of the list of subjects to be used for testing the model

        Returns
        -------

        train_subjects: [str]
            A list of subjects used for training

        test_subjects: [str]
            A list of subjects used for testing
        '''

        train_subjects = []
        test_subjects = []
        for train_list in train_subject_lists:
            subject_list = pd.read_csv(os.path.join(self._root, "subject", train_list+".txt"))
            train_subjects.append(subject_list.values)
        for test_list in test_subject_lists:
            subject_list = pd.read_csv(os.path.join(self._root, "subject", test_list+".txt"))
            test_subjects.append(subject_list.values)

        train_subjects = np.concatenate(train_subjects)
        test_subjects = np.concatenate(test_subjects)
        if self._parse_method == "form":
        # For the form method, the "subject names" do not match the ones provided
        # in the file. This clause transforms the subject names to match the file.
            
            new_train_subjects = []
            for i in train_subjects:
                form_subject_number = i[0].split("-")[0] + "-" + i[0].split("-")[1]
                new_train_subjects.append(form_subject_number)
            new_test_subjects = []
            for i in test_subjects:
                form_subject_number = i[0].split("-")[0] + "-" + i[0].split("-")[1]
                new_test_subjects.append(form_subject_number)
            train_subjects, test_subjects = new_train_subjects, new_test_subjects
        return train_subjects, test_subjects

    def _convert_subject_list(self, subject_list):
        ''' Function to convert the list of subjects for the "word" parse method
        
        Parameters
        ----------
        
        subject_lists: [str]
            A list of subjects

        Returns
        -------

        subject_lists: [str]
            A list of subjects that is compatible with the "word" parse method

        '''

        if self._parse_method == "word":
            new_subject_list = []
            for sub in subject_list:
                new_subject_number = "-".join(sub.split("-")[:3])
                new_subject_list.append(new_subject_number)
            return new_subject_list
        else:
            return subject_list
                
    def _get_data(self):
        ''' Function to get the data and to extract the data for training or testing
        
        Returns
        -------

        pd.DataFram
            A dataframe (subject, image, and output) that contains only the training/testing data

        '''

        # Get the data
        if not os.path.isdir(self._root):
            os.makedirs(self._root)

        if os.path.isfile(self.image_data_file_name):
            logging.info("Loading data from pickle")
            images_data = pickle.load(open(self.image_data_file_name, 'rb'))
        else:
            self._download_xml()
            self._download_data()
            images_data = self._process_data()

        # Extract train or test data out
        self._download_subject_list()
        train_subjects, test_subjects = self._process_subjects()
        if self._train:
            data = images_data[np.in1d(self._convert_subject_list(images_data["subject"]),
                                       train_subjects)]
        else:
            data = images_data[np.in1d(self._convert_subject_list(images_data["subject"]),
                                       test_subjects)]
        return data

    def __getitem__(self, idx):
        return (self._data[0].iloc[idx].image, self._data[0].iloc[idx].output)
